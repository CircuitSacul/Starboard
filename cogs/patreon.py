import asyncio
from discord.ext import tasks, commands
import os

from aiohttp_requests import requests

from patreon.schemas import campaign
from patreon.jsonapi.parser import JSONAPIParser
from patreon.jsonapi.url_util import build_url
from patreon.utils import user_agent_string
from patreon.version_compatibility.utc_timezone import utc_timezone
from six.moves.urllib.parse import urlparse, parse_qs, urlencode


class API(object):
    def __init__(self, access_token):
        super(API, self).__init__()
        self.access_token = access_token

    async def fetch_user(self, includes=None, fields=None):
        return await self.__get_jsonapi_doc(
            build_url('current_user', includes=includes, fields=fields)
        )

    async def fetch_campaign_and_patrons(self, includes=None, fields=None):
        if not includes:
            includes = campaign.default_relationships \
                + [campaign.Relationships.pledges]
        return await self.fetch_campaign(includes=includes, fields=fields)

    async def fetch_campaign(self, includes=None, fields=None):
        return await self.__get_jsonapi_doc(
            build_url(
                'current_user/campaigns', includes=includes, fields=fields
            )
        )

    async def fetch_page_of_pledges(
            self, campaign_id, page_size, cursor=None, includes=None,
            fields=None
    ):
        url = 'campaigns/{0}/pledges'.format(campaign_id)
        params = {'page[count]': page_size}
        if cursor:
            try:
                cursor = self.__as_utc(cursor).isoformat()
            except AttributeError:
                pass
            params.update({'page[cursor]': cursor})
        url += "?" + urlencode(params)
        return await self.__get_jsonapi_doc(
            build_url(url, includes=includes, fields=fields)
        )

    @staticmethod
    async def extract_cursor(jsonapi_document, cursor_path='links.next'):
        def head_and_tail(path):
            if path is None:
                return None, None
            head_tail = path.split('.', 1)
            return head_tail if len(head_tail) == 2 else (head_tail[0], None)

        if isinstance(jsonapi_document, JSONAPIParser):
            jsonapi_document = jsonapi_document.json_data

        head, tail = head_and_tail(cursor_path)
        current_dict = jsonapi_document
        while head and type(current_dict) == dict and head in current_dict:
            current_dict = current_dict[head]
            head, tail = head_and_tail(tail)

        # Path was valid until leaf, at which point nothing was found
        if current_dict is None or (head is not None and tail is None):
            return None
        # Path stopped before leaf was reached
        elif current_dict and type(current_dict) != str:
            raise Exception(
                'Provided cursor path did not result in a link', current_dict
            )

        link = current_dict
        query_string = urlparse(link).query
        parsed_query_string = parse_qs(query_string)
        if 'page[cursor]' in parsed_query_string:
            return parsed_query_string['page[cursor]'][0]
        else:
            return None

    # Internal methods
    async def __get_jsonapi_doc(self, suffix):
        response_json = await self.__get_json(suffix)
        if response_json.get('errors'):
            return response_json
        return JSONAPIParser(response_json)

    async def __get_json(self, suffix):
        response = await requests.get(
            "https://www.patreon.com/api/oauth2/api/{}".format(suffix),
            headers={
                'Authorization': "Bearer {}".format(self.access_token),
                'User-Agent': user_agent_string(),
            }
        )
        return await response.json()

    @staticmethod
    def __as_utc(dt):
        if hasattr(dt, 'tzinfo'):
            if dt.tzinfo:
                return dt.astimezone(utc_timezone())
            else:
                return dt.replace(tzinfo=utc_timezone())
        return dt


class Patreon(commands.Cog):
    """Handles interactions with Patreon"""
    def __init__(self, bot):
        self.bot = bot

        self.access_token = os.getenv("PATREON_TOKEN")
        self.client = API(self.access_token)

    async def get_all_patrons(self):
        """Get the list of all patrons
        --
        @return list"""

        # If the client doesn't exist
        if self.client is None:
            print("Error : Patron API client not defined")
            return

        patrons = []

        # Get the campaign id
        campaign_resource = await self.client.fetch_campaign()
        campaign_id = campaign_resource.data()[0].id()

        # Get all the pledgers
        all_pledgers = []    # Contains the list of all pledgers
        cursor = None  # Allows us to walk through pledge pages
        stop = False

        while not stop:
            # Get the resources of the current pledge page
            # Each page contains 25 pledgers, also
            # fetches the pledge info such as the total
            # $ sent and the date of pledge end
            pledge_resource = await self.client.fetch_page_of_pledges(
                campaign_id, 25,
                cursor=cursor,
                fields={
                    "pledge": [
                        "total_historical_amount_cents",
                        "declined_since"
                    ]
                }
            )

            # Update cursor
            cursor = await self.client.extract_cursor(pledge_resource)

            # Add data to the list of pledgers
            all_pledgers += pledge_resource.data()

            # If there is no more page, stop the loop
            if not cursor:
                stop = True
                break

        # Get the pledgers info and add the premium status
        for pledger in all_pledgers:
            await asyncio.sleep(0)

            payment = 0
            total_paid = 0
            is_declined = False

            # Get the date of declined pledge
            # False if the pledge has not been declined
            declined_since = pledger.attribute("declined_since")
            total_paid = pledger.attribute("total_historical_amount_cents")/100

            # Get the pledger's discord ID
            try:
                discord_id = int(pledger.relationship("patron").attribute(
                    "social_connections")["discord"]["user_id"])
            except Exception:
                discord_id = None

            # Get the reward tier of the player
            if pledger.relationships()["reward"]["data"]:
                payment = int(pledger.relationship(
                    "reward").attribute("amount_cents") / 100)

            # Check if the patron has declined his pledge
            if declined_since is not None:
                is_declined = True

            # Add patron data to the patrons list
            patrons.append(
                {
                    "name": pledger.relationship("patron").attribute(
                        "first_name"),
                    "payment": int(payment),
                    "declined": is_declined,
                    "total": int(total_paid),
                    "discord_id": discord_id
                }
            )

        return patrons

    @commands.command(name='patrons')
    @commands.is_owner()
    async def get_patrons(self, ctx):
        await ctx.send(await self.get_all_patrons())


def setup(bot):
    bot.add_cog(Patreon(bot))
