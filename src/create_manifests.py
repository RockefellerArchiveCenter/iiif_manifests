import json
import logging
import traceback
from io import BytesIO
from os import getenv

import iiif_prezi3
import shortuuid
from PIL import Image

from .clients import ArchivesSpaceClient, AWSClient, ZodiacClient

logging.basicConfig(
    level=int(getenv('LOGGING_LEVEL', logging.INFO)),
    format='%(filename)s::%(funcName)s::%(lineno)s %(message)s')
logging.getLogger("bagit").setLevel(logging.ERROR)


class ManifestMaker(object):

    def __init__(self,
                 package_id,
                 aws_region,
                 aws_role_arn,
                 ssm_parameter_path,
                 destination_bucket,
                 sns_topic):
        """Create IIIF Manifest."""
        self.package_id = package_id
        self.aws_region = aws_region
        self.aws_role_arn = aws_role_arn
        self.destination_bucket = destination_bucket
        self.sns_topic = sns_topic
        self.service_name = 'iiif_manifests'
        self.config = self.get_config(ssm_parameter_path)
        self.as_client = ArchivesSpaceClient(
            self.config.get('AS_BASEURL'),
            self.config.get('AS_USERNAME'),
            self.config.get('AS_PASSWORD'),
            self.config.get('AS_REPO'))
        self.zodiac_client = ZodiacClient(self.config['ZODIAC_BASEURL'])

    def run(self):
        """Main class method which calls all other methods."""
        try:
            self.send_start_message()
            package_data = self.zodiac_client.get(f'packages/{self.package_id}')
            as_uri = package_data['identifiers']['archivesspace_archival_object']
            as_data = self.as_client.get_object(as_uri)
            dimes_id = shortuuid.uuid(name=as_uri)
            manifest_files = self.list_objects(self.destination_bucket, f'images/{dimes_id}')
            manifest = self.create_manifest(as_data, manifest_files)
            self.upload_manifest(manifest, self.destination_bucket, dimes_id)
            self.send_success_message()
        except Exception as e:
            self.send_failure_message(e)

    def get_config(self, ssm_parameter_path):
        """Fetch config values from Parameter Store.

        Returns:
            configuration (dict): all parameters found at the supplied path.
        """
        configuration = {}
        ssm_client = AWSClient(self.aws_role_arn).get_client('ssm', self.aws_region)
        try:
            paginator = ssm_client.get_paginator('get_parameters_by_path')
            response_iterator = paginator.paginate(Path=ssm_parameter_path)
            for page in response_iterator:
                for entry in page['Parameters']:
                    param_path_array = entry.get('Name').split("/")
                    section_position = len(param_path_array) - 1
                    section_name = param_path_array[section_position]
                    configuration[section_name] = entry.get('Value')
        except BaseException:
            logging.error("Encountered an error loading config from SSM.")
            traceback.print_exc()
        finally:
            return configuration

    def list_objects(self, bucket, prefix=None):
        """Return a sorted list of keys in a bucket.

        Args:
            bucket (string): name of bucket containing objects.
            prefix (string): optional prefix to filter by.

        Returns:
            objects (list): list of object keys, sorted in increasing order.
        """
        objects = []
        s3_client = AWSClient(self.aws_role_arn).get_client('s3', self.aws_region)
        paginator = s3_client.get_paginator('list_objects_v2')
        results = paginator.paginate(Bucket=bucket, Prefix=prefix)
        for page in results:
            objects += [item["Key"] for item in page.get("Contents", [])]
        return sorted(objects)

    def get_page_number(self, filename):
        """Parse a page number from a filename.

        Presumes that:
            The page number is preceded by an underscore
            The page number is immediately followed by either by `_m`, `_me` or `_se`,
            or the file extension.

        Args:
            filename (str): filename of an image file.

        Returns:
            4-digit page number from the filename with leading zeroes
        """
        if "_se" in filename:
            filename_trimmed = filename.split("_se")[0]
        elif "_m" in filename:
            filename_trimmed = filename.split("_m")[0]
        else:
            filename_trimmed = filename
        return filename_trimmed.split("_")[-1].lstrip("0").zfill(4)

    def get_image_dimensions(self, key):
        """Get dimensions recorded in an object's metadata.

        If the attributes are unavailable in the object's metadata, downloads
        the file locally to determine dimensions, then updates metadata in AWS.

        Args:
            key (str): key for the object.
        """
        s3_client = AWSClient(self.aws_role_arn).get_client('s3', self.aws_region)
        metadata = s3_client.head_object(Bucket=self.destination_bucket, Key=key).get("Metadata", {})
        try:
            width = int(metadata["width"])
            height = int(metadata["height"])
        except KeyError:
            """Get data from image file."""
            range_header = 'bytes=0-8191'
            response = s3_client.get_object(Bucket=self.destination_bucket, Key=key, Range=range_header)
            partial_data = response['Body'].read()
            try:
                img = Image.open(BytesIO(partial_data))
                width, height = img.size
            except Exception:
                """Fallback to full download if partial data is insufficient."""
                full_response = s3_client.get_object(Bucket=self.destination_bucket, Key=key)
                full_data = full_response['Body'].read()
                img = Image.open(BytesIO(full_data))
                width, height = img.size
            metadata.update({"width": str(width), "height": str(height)})
            s3_client.copy_object(
                Bucket=self.destination_bucket,
                Key=key,
                CopySource={"Bucket": self.destination_bucket, "Key": key},
                ContentType='image/jp2',
                Metadata=metadata,
                MetadataDirective="REPLACE")
        return width, height

    def create_manifest(self, obj_data, manifest_files):
        """Create IIIF manifest.

        Args:
            obj_data (dict): metadata about the object to be embedded in the manifest.
            manifest_files (list): Files to be included in the manifest.
        """
        manifest_id = f"{self.config.get('IIIF_URL').rstrip('/')}/manifests/{self.package_id}"
        manifest = iiif_prezi3.Manifest(id=manifest_id, label=obj_data["title"])
        manifest.add_metadata("Date", obj_data["dates"])
        for jp2_key in manifest_files:
            page_number = self.get_page_number(jp2_key).lstrip("0")
            jp2_filename = jp2_key.split("/")[-1]
            width, height = self.get_image_dimensions(jp2_key)
            """Set the canvas ID, which starts the same as the manifest ID,
            and then include page_number as the canvas ID.
            """
            canvas_id = f"{manifest_id}/canvas/{page_number}"
            service = iiif_prezi3.ServiceItem(
                id=f"{self.config.get('IIIF_IMAGE_API_BASEURL').rstrip('/')}/{jp2_filename}",
                type="ImageService3",
                profile="level2")
            # TODO this should be possible to create via an IIIF Prezi object
            thumbnail = [{
                "id": f"{self.config.get('IIIF_IMAGE_API_BASEURL').rstrip('/')}/{jp2_filename}/square/200,/0/default.jpg",
                "type": "Image",
                "format": "image/jpeg",
                "height": 200,
                "width": 200,
                "service": json.loads(service.jsonld())
            }]
            canvas = manifest.make_canvas(
                id=canvas_id,
                height=height,
                width=width,
                label=f"Page {page_number}",
                thumbnail=thumbnail)
            canvas.add_image(
                anno_page_id=f"{canvas_id}/annotation-page/1",
                anno_id=f"{canvas_id}/annotation/1",
                image_url=f"{self.config.get('IIIF_IMAGE_API_BASEURL').rstrip('/')}/{jp2_filename}/full/max/0/default.jpg",
                format="image/jpeg",
                height=height,
                width=width,
                service=service)
        return json.loads(manifest.jsonld())

    def upload_manifest(self, manifest, bucket, dimes_id):
        """Upload manifest to destination.

        Args:
            manifest (dict): Content of object to be uploaded
            bucket: Bucket to which file object should be uploaded
            dimes_id: DIMES identifier for manifest
        """
        s3_client = AWSClient(self.aws_role_arn).get_client('s3', self.aws_region)
        bytes_manifest = json.dumps(manifest).encode('utf-8')
        s3_client.put_object(
            Bucket=bucket,
            Key=f'manifests/{dimes_id}',
            Body=bytes_manifest)

    def send_start_message(self):
        """Send start message to SNS topic."""
        client = AWSClient(self.aws_role_arn).get_client('sns', self.aws_region)
        client.publish(
            TopicArn=self.sns_topic,
            MessageGroupId=f'{self.service_name}-{self.package_id}',
            MessageDeduplicationId=f'{self.service_name}-{self.package_id}-start',
            Message='IIIF Manifest creation started.',
            MessageAttributes={
                'package_id': {
                    'DataType': 'String',
                    'StringValue': self.package_id,
                },
                'service': {
                    'DataType': 'String',
                    'StringValue': self.service_name,
                },
                'outcome': {
                    'DataType': 'String',
                    'StringValue': 'STARTED',
                },
                'message': {
                    'DataType': 'String',
                    'StringValue': 'IIIF Manifest creation started.',
                }
            })
        logging.debug('Start notification delivered.')

    def send_success_message(self, package_data):
        """Send success message to SNS topic.

        Args:
            package_data (dict): data about package
        """
        client = AWSClient(self.aws_role_arn).get_client('sns', self.aws_region)
        client.publish(
            TopicArn=self.sns_topic,
            MessageGroupId=f'{self.service_name}-{self.package_id}',
            MessageDeduplicationId=f'{self.service_name}-{self.package_id}-success',
            Message=json.dumps(package_data, default=str),
            MessageAttributes={
                'package_id': {
                    'DataType': 'String',
                    'StringValue': self.package_id,
                },
                'service': {
                    'DataType': 'String',
                    'StringValue': self.service_name,
                },
                'outcome': {
                    'DataType': 'String',
                    'StringValue': 'SUCCESS',
                },
                'message': {
                    'DataType': 'String',
                    'StringValue': 'IIIF Manifest created.'
                }
            })
        logging.debug(f'Success message sent for {self.package_id}')

    def send_failure_message(self, exception):
        """Send failure message to SNS topic.

        Args:
            exception (Exception): the error that was thrown.
        """
        client = AWSClient(self.aws_role_arn).get_client('sns', self.aws_region)
        tb = ''.join(traceback.format_exception(exception)[:-1])
        client.publish(
            TopicArn=self.sns_topic,
            MessageGroupId=f'{self.service_name}-{self.package_id}',
            MessageDeduplicationId=f'{self.service_name}-{self.package_id}-failure',
            Message=tb,
            MessageAttributes={
                'package_id': {
                    'DataType': 'String',
                    'StringValue': self.package_id,
                },
                'service': {
                    'DataType': 'String',
                    'StringValue': self.service_name,
                },
                'outcome': {
                    'DataType': 'String',
                    'StringValue': 'FAILURE',
                },
                'message': {
                    'DataType': 'String',
                    'StringValue': str(exception),
                }
            })
        logging.debug(f'Failure message sent for {self.package_id}')


if __name__ == '__main__':
    package_id = getenv('PACKAGE_ID')
    aws_region = getenv('AWS_REGION')
    aws_role_arn = getenv('AWS_ROLE_ARN')
    destination_bucket = getenv('AWS_DESTINATION_BUCKET')
    sns_topic = getenv('AWS_SNS_TOPIC')
    ssm_parameter_path = f"/{getenv('ENV')}/{getenv('APP_CONFIG_PATH')}"

    ManifestMaker(
        package_id,
        aws_region,
        aws_role_arn,
        ssm_parameter_path,
        destination_bucket,
        sns_topic).run()
