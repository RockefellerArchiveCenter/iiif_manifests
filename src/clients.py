
import boto3
from asnake import utils
from asnake.aspace import ASpace
from aws_assume_role_lib import assume_role
from requests import Session
from requests.exceptions import HTTPError


class ZodiacClientError(Exception):
    pass


class ArchivesSpaceClient:
    def __init__(self, baseurl, username, password, repository):
        self.client = ASpace(
            baseurl=baseurl,
            username=username,
            password=password,
            repository=repository).client
        self.repository = repository

    def get_object(self, uri):
        """Gets archival object title and date.

        Args:
            uri (str): an ArchivesSpace URI.
        Returns:
            obj (dict): A dictionary representation of an archival object from ArchivesSpace.
        """
        obj = self.client.get(uri).json()
        obj["dates"] = utils.find_closest_value(obj, 'dates', self.client)
        return self.format_data(obj)

    def format_data(self, data):
        """Parses ArchivesSpace data.

        Args:
            data (dict): ArchivesSpace data.
        Returns:
            parsed (dict): Parsed data, with only required fields present.
        """
        title = data.get("title", data.get("display_string")).title()
        dates = ", ".join([utils.get_date_display(d, self.client)
                           for d in data.get("dates", [])])
        return {"title": title, "dates": dates, "uri": data["uri"]}


class AWSClient:
    def __init__(self, role_arn):
        """Gets Boto3 SNS client which authenticates with a specific IAM role."""
        session = boto3.Session()
        self.assumed_role_session = assume_role(session, role_arn)

    def get_client(self, resource, region_name):
        return self.assumed_role_session.client(resource, region_name=region_name)


class ZodiacClient(object):

    def __init__(self, baseurl):
        self.session = Session()
        self.session.headers.update({
            'Accept': 'application/json',
        })
        self.baseurl = baseurl.rstrip('/')

    def get(self, uri):
        """Makes an HTTP GET request"""
        url = f'{self.baseurl}/{uri.lstrip("/")}'
        try:
            resp = self.session.get(url)
            resp.raise_for_status()
            return resp.json()
        except HTTPError:
            raise ZodiacClientError(f"Error fetching url {url}: {resp.status_code} {resp.text}")
