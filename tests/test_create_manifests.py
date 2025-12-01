import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import boto3
from moto import mock_aws
from moto.core import DEFAULT_ACCOUNT_ID

from src.create_manifests import ManifestMaker

DEFAULT_ARGS = ['0edb4066-980c-491f-bd73-c80a6546ff6d',
                'us-east-1',
                'arn:aws:iam::123456789012:role/iiif-manifest-role',
                '/dev/iiif-manifest',
                'raciiif-dev',
                'sns-topic']

DEFAULT_CONFIG = {
    "ZODIAC_BASEURL": "https://zodiac-backend.dev.rockarch.org",
    "IIIF_URL": "https://iiif.dev.rockarch.org",
    "IIIF_IMAGE_API_BASEURL": "https://images.rockarch.org/iiif/3",
    "AS_BASEURL": "https://sandbox.as.org",
    "AS_USERNAME": "admin",
    "AS_PASSWORD": "admin",
    "AS_REPO": "2"
}


@patch('src.clients.ArchivesSpaceClient.__init__')
@patch('src.create_manifests.ManifestMaker.get_config')
def set_up_manifest_maker(mock_config, mock_as):
    mock_as.return_value = None
    mock_config.return_value = DEFAULT_CONFIG
    return ManifestMaker(*DEFAULT_ARGS)


class ManifestInitTests(TestCase):

    @patch('src.clients.ArchivesSpaceClient.__init__')
    @patch('src.create_manifests.ManifestMaker.get_config')
    def test_init(self, mock_config, mock_as):
        mock_as.return_value = None
        mock_config.return_value = DEFAULT_CONFIG
        ManifestMaker(*DEFAULT_ARGS)
        mock_as.assert_called_once_with(
            "https://sandbox.as.org",
            "admin",
            "admin",
            "2")
        mock_config.assert_called_once_with('/dev/iiif-manifest')


class ManifestMethodTests(TestCase):

    ZODIAC_DATA = {
        "identifier": "0edb4066-980c-491f-bd73-c80a6546ff6d",
        "title": "Original Audio Tapes",
        "origin": "aurora",
        "status": "IN PROCESS",
        "identifiers": {
            "aurora_package": "https://aurora.dev.rockarch.org/api/transfers/1631/?format=json",
            "aurora_accession": "https://aurora.dev.rockarch.org/api/accessions/237/",
            "archivematica_uuid": "0a9c6171-a18d-4ff6-b9e7-bef01aaded10",
            "archivesspace_accession": None,
            "archivesspace_archival_object": "/repositories/2/archival_objects/2153"
        }
    }
    AS_DATA = {
        "title": "Original Audio Tapes",
        "dates": "1950s-1980s (Bulk 1982)",
        "uri": "/repositories/101/archival_objects/2336"
    }

    @patch('src.create_manifests.ManifestMaker.send_failure_message')
    @patch('src.create_manifests.ManifestMaker.send_success_message')
    @patch('src.create_manifests.ManifestMaker.upload_manifest')
    @patch('src.create_manifests.ManifestMaker.create_manifest')
    @patch('src.create_manifests.ManifestMaker.list_objects')
    @patch('src.clients.ArchivesSpaceClient.get_object')
    @patch('src.clients.ZodiacClient.get')
    @patch('src.create_manifests.ManifestMaker.send_start_message')
    def test_run(self,
                 mock_start_message,
                 mock_get_data,
                 mock_get_as_data,
                 mock_list_objects,
                 mock_create_manifest,
                 mock_upload_manifest,
                 mock_success_message,
                 mock_failure_message):
        manifest_maker = set_up_manifest_maker()
        mock_get_data.return_value = self.ZODIAC_DATA
        mock_get_as_data.return_value = self.AS_DATA
        mock_list_objects.return_value = []

        manifest_maker.run()

        mock_start_message.assert_called_once_with()
        mock_get_data.assert_called_once_with('packages/0edb4066-980c-491f-bd73-c80a6546ff6d')
        mock_get_as_data.assert_called_once_with('/repositories/2/archival_objects/2153')
        mock_list_objects.assert_called_once_with('raciiif-dev', 'images/BbgfpzAC8AGr8DLokEzacp')
        mock_create_manifest.assert_called_once_with(self.AS_DATA, [])
        mock_upload_manifest.assert_called_once_with(
            mock_create_manifest(), 'raciiif-dev', 'BbgfpzAC8AGr8DLokEzacp')
        mock_success_message.assert_called_once_with()
        mock_failure_message.assert_not_called()

    @patch('src.create_manifests.ManifestMaker.send_failure_message')
    @patch('src.create_manifests.ManifestMaker.send_success_message')
    @patch('src.create_manifests.ManifestMaker.upload_manifest')
    @patch('src.create_manifests.ManifestMaker.create_manifest')
    @patch('src.create_manifests.ManifestMaker.list_objects')
    @patch('src.clients.ArchivesSpaceClient.get_object')
    @patch('src.clients.ZodiacClient.get')
    @patch('src.create_manifests.ManifestMaker.send_start_message')
    def test_run_with_exception(self,
                                mock_start_message,
                                mock_get_data,
                                mock_get_as_data,
                                mock_list_objects,
                                mock_create_manifest,
                                mock_upload_manifest,
                                mock_success_message,
                                mock_failure_message):
        manifest_maker = set_up_manifest_maker()
        exception = Exception('foo')
        mock_get_data.side_effect = exception

        manifest_maker.run()

        mock_start_message.assert_called_once_with()
        mock_get_data.assert_called_once_with('packages/0edb4066-980c-491f-bd73-c80a6546ff6d')
        mock_get_as_data.assert_not_called()
        mock_list_objects.assert_not_called()
        mock_create_manifest.assert_not_called()
        mock_upload_manifest.assert_not_called()
        mock_success_message.assert_not_called()
        mock_failure_message.assert_called_once_with(exception)

    @mock_aws
    def test_get_config(self):
        ssm = boto3.client('ssm', region_name='us-east-1')
        manifest_maker = set_up_manifest_maker()
        parameter_path = "dev/iiif_manifests"
        for name, value in [("foo", "bar"), ("baz", "buzz")]:
            ssm.put_parameter(
                Name=f"{parameter_path}/{name}",
                Value=value,
                Type="String")
        output = manifest_maker.get_config(parameter_path)
        self.assertEqual(output, {"foo": "bar", "baz": "buzz"})

    @mock_aws
    def test_list_objects(self):
        manifest_maker = set_up_manifest_maker()
        package_id = 'BbgfpzAC8AGr8DLokEzacp'
        s3 = boto3.client('s3', region_name='us-east-1')
        s3.create_bucket(Bucket=manifest_maker.destination_bucket)
        for x in range(10):
            s3.put_object(
                Body=b'text',
                Bucket=manifest_maker.destination_bucket,
                Key=f'images/{package_id}_{x}.jp2'
            )
        output = manifest_maker.list_objects(manifest_maker.destination_bucket, f'images/{package_id}')
        self.assertEqual(
            output,
            [
                f'images/{package_id}_0.jp2',
                f'images/{package_id}_1.jp2',
                f'images/{package_id}_2.jp2',
                f'images/{package_id}_3.jp2',
                f'images/{package_id}_4.jp2',
                f'images/{package_id}_5.jp2',
                f'images/{package_id}_6.jp2',
                f'images/{package_id}_7.jp2',
                f'images/{package_id}_8.jp2',
                f'images/{package_id}_9.jp2'
            ])

    def test_get_page_number(self):
        manifest_maker = set_up_manifest_maker()
        for input, expected in [
                ("image/BbgfpzAC8AGr8DLokEzacp_0001_se", "0001"),
                ("image/BbgfpzAC8AGr8DLokEzacp_0001_m", "0001"),
                ("image/BbgfpzAC8AGr8DLokEzacp_001", "0001"),
                ("image/BbgfpzAC8AGr8DLokEzacp_1", "0001")
        ]:
            output = manifest_maker.get_page_number(input)
            self.assertEqual(output, expected)

    @mock_aws
    def test_get_image_dimensions(self):
        fixture_path = Path('tests', 'fixtures', 'test.jp2')
        manifest_maker = set_up_manifest_maker()
        s3 = boto3.client('s3', region_name='us-east-1')
        s3.create_bucket(Bucket=manifest_maker.destination_bucket)
        s3.upload_file(
            Filename=fixture_path,
            Bucket=manifest_maker.destination_bucket,
            Key='no_metadata.jp2',)
        s3.upload_file(
            Filename=fixture_path,
            Bucket=manifest_maker.destination_bucket,
            Key='metadata.jp2',
            ExtraArgs={
                'Metadata': {"width": "300", "height": "500"}
            })
        output = manifest_maker.get_image_dimensions('no_metadata.jp2')
        self.assertEqual(output, (2717, 3701))
        metadata = s3.head_object(
            Bucket=manifest_maker.destination_bucket,
            Key='no_metadata.jp2').get(
            "Metadata",
            {})
        self.assertEqual(int(metadata["width"]), 2717)
        self.assertEqual(int(metadata["height"]), 3701)

        output = manifest_maker.get_image_dimensions('metadata.jp2')
        self.assertEqual(output, (300, 500))

    @patch('src.create_manifests.ManifestMaker.get_image_dimensions')
    def test_create_manifest(self, mock_dimensions):
        mock_dimensions.return_value = 200, 200
        manifest_maker = set_up_manifest_maker()
        items = ["manifests/BbgfpzAC8AGr8DLokEzacp_0001"]
        expected = {
            '@context': 'http://iiif.io/api/presentation/3/context.json',
            'id': 'https://iiif.dev.rockarch.org/manifests/0edb4066-980c-491f-bd73-c80a6546ff6d',
            'type': 'Manifest',
            'label': {'none': ['Original Audio Tapes']},
            'metadata': [{'label': {'none': ['Date']}, 'value': {'none': ['1950s-1980s (Bulk 1982)']}}],
            'items': [
                {
                    'id': 'https://iiif.dev.rockarch.org/manifests/0edb4066-980c-491f-bd73-c80a6546ff6d/canvas/1',
                    'type': 'Canvas',
                    'label': {'none': ['Page 1']},
                    'height': 200,
                    'width': 200,
                    'thumbnail': [
                        {
                            'id': 'https://images.rockarch.org/iiif/3/BbgfpzAC8AGr8DLokEzacp_0001/square/200,/0/default.jpg',
                            'type': 'Image',
                            'height': 200,
                            'width': 200,
                            'service': [
                                {
                                    'id': 'https://images.rockarch.org/iiif/3/BbgfpzAC8AGr8DLokEzacp_0001',
                                    'type': 'ImageService3',
                                    'profile': 'level2'
                                }
                            ],
                            'format': 'image/jpeg'
                        }
                    ],
                    'items': [
                        {
                            'id': 'https://iiif.dev.rockarch.org/manifests/0edb4066-980c-491f-bd73-c80a6546ff6d/canvas/1/annotation-page/1',
                            'type': 'AnnotationPage',
                            'items': [
                                {
                                    'id': 'https://iiif.dev.rockarch.org/manifests/0edb4066-980c-491f-bd73-c80a6546ff6d/canvas/1/annotation/1',
                                    'type': 'Annotation',
                                    'motivation': 'painting',
                                    'body': {
                                        'id': 'https://images.rockarch.org/iiif/3/BbgfpzAC8AGr8DLokEzacp_0001/full/max/0/default.jpg',
                                        'type': 'Image',
                                        'height': 200,
                                        'width': 200,
                                        'service': [
                                            {
                                                'id': 'https://images.rockarch.org/iiif/3/BbgfpzAC8AGr8DLokEzacp_0001',
                                                'type': 'ImageService3',
                                                'profile': 'level2'
                                            }],
                                        'format': 'image/jpeg'
                                    },
                                    'target': 'https://iiif.dev.rockarch.org/manifests/0edb4066-980c-491f-bd73-c80a6546ff6d/canvas/1'
                                }
                            ]
                        }
                    ]
                },
            ]
        }
        output = manifest_maker.create_manifest(self.AS_DATA, items)
        self.assertEqual(output, expected)

    @mock_aws
    def test_upload_manifest(self):
        manifest_maker = set_up_manifest_maker()
        dimes_id = 'BbgfpzAC8AGr8DLokEzacp'
        s3 = boto3.client('s3', region_name='us-east-1')
        s3.create_bucket(Bucket=manifest_maker.destination_bucket)
        manifest_maker.upload_manifest({}, manifest_maker.destination_bucket, dimes_id)
        objects = s3.list_objects_v2(Bucket=manifest_maker.destination_bucket, Prefix='manifests')
        self.assertEqual(objects['KeyCount'], 1)
        self.assertEqual(objects['Contents'][0]['Key'], f'manifests/{dimes_id}')


class MessageTests(TestCase):

    def set_up_sns(self, sns):
        topic_arn = sns.create_topic(
            Name='my-topic.fifo',
            Attributes={
                "FifoTopic": "true",
                "ContentBasedDeduplication": "true"
            }
        )['TopicArn']
        sqs_conn = boto3.resource("sqs", region_name="us-east-1")
        queue_name = "test-queue.fifo"
        sqs_conn.create_queue(
            QueueName=queue_name,
            Attributes={
                "FifoQueue": "true",
                "ContentBasedDeduplication": "true"
            }
        )
        sns.subscribe(
            TopicArn=topic_arn,
            Protocol="sqs",
            Endpoint=f"arn:aws:sqs:us-east-1:{DEFAULT_ACCOUNT_ID}:{queue_name}",
        )
        return topic_arn, sqs_conn, queue_name

    @mock_aws
    @patch('src.clients.AWSClient.get_client')
    def test_send_start_message(self, mock_role):
        """Asserts success messages are delivered as expected."""
        sns = boto3.client('sns', region_name='us-east-1')
        mock_role.return_value = sns
        topic_arn, sqs_conn, queue_name = self.set_up_sns(sns)

        manifest_maker = set_up_manifest_maker()
        manifest_maker.sns_topic = topic_arn

        manifest_maker.send_start_message()

        queue = sqs_conn.get_queue_by_name(QueueName=queue_name)
        messages = queue.receive_messages(MaxNumberOfMessages=1)
        message_body = json.loads(messages[0].body)
        assert message_body['Message'] == 'IIIF Manifest creation started.'
        assert message_body['MessageAttributes']['outcome']['Value'] == 'STARTED'
        assert message_body['MessageAttributes']['package_id']['Value'] == manifest_maker.package_id
        assert message_body['MessageAttributes']['service']['Value'] == manifest_maker.service_name
        assert message_body['MessageAttributes']['message']['Value'] == 'IIIF Manifest creation started.'

    @mock_aws
    @patch('src.clients.AWSClient.get_client')
    def test_send_success_message(self, mock_role):
        """Asserts success messages are delivered as expected."""
        sns = boto3.client('sns', region_name='us-east-1')
        mock_role.return_value = sns
        topic_arn, sqs_conn, queue_name = self.set_up_sns(sns)

        manifest_maker = set_up_manifest_maker()
        manifest_maker.sns_topic = topic_arn

        package_data = {}
        manifest_maker.send_success_message(package_data)

        queue = sqs_conn.get_queue_by_name(QueueName=queue_name)
        messages = queue.receive_messages(MaxNumberOfMessages=1)
        message_body = json.loads(messages[0].body)
        assert message_body['Message'] == json.dumps(package_data)
        assert message_body['MessageAttributes']['outcome']['Value'] == 'SUCCESS'
        assert message_body['MessageAttributes']['package_id']['Value'] == manifest_maker.package_id
        assert message_body['MessageAttributes']['service']['Value'] == manifest_maker.service_name
        assert message_body['MessageAttributes']['message']['Value'] == 'IIIF Manifest created.'

    @mock_aws
    @patch('src.clients.AWSClient.get_client')
    @patch('traceback.format_exception')
    def test_send_failure_message(self, mock_traceback, mock_role):
        """Asserts failure messages are delivered as expected."""
        sns = boto3.client('sns', region_name='us-east-1')
        mock_role.return_value = sns
        topic_arn, sqs_conn, queue_name = self.set_up_sns(sns)

        manifest_maker = set_up_manifest_maker()
        manifest_maker.sns_topic = topic_arn
        exception_message = "foo"
        exception = Exception(exception_message)
        mock_traceback.return_value = ["baz", "buzz"]

        manifest_maker.send_failure_message(exception)

        queue = sqs_conn.get_queue_by_name(QueueName=queue_name)
        messages = queue.receive_messages(MaxNumberOfMessages=1)
        message_body = json.loads(messages[0].body)
        assert message_body['Message'] == "baz"
        assert message_body['MessageAttributes']['outcome']['Value'] == 'FAILURE'
        assert message_body['MessageAttributes']['package_id']['Value'] == manifest_maker.package_id
        assert exception_message in message_body['MessageAttributes']['message']['Value']
