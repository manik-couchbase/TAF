import json

from capellaAPI.capella.dedicated.CapellaAPI import CapellaAPI
from pytests.basetestcase import BaseTestCase
from urlparse import urljoin, urlparse
from .sso_utils import SSOComponents
from .saml_response import SAMLResponse
from .saml_signatory import SAMLSignatory


class SSOTest(BaseTestCase):
    def setUp(self):
        super(SSOTest, self).setUp()
        self.url = self.input.capella.get("pod")
        self.user = self.input.capella.get("capella_user")
        self.passwd = self.input.capella.get("capella_pwd")
        self.tenant_id = self.input.capella.get("tenant_id")
        self.secret_key = self.input.capella.get("secret_key")
        self.access_key = self.input.capella.get("access_key")
        self.project_id = self.input.capella.get("project")
        self.cluster_id = self.input.capella.get("clusters")
        if self.input.capella.get("test_users"):
            self.test_users = json.loads(self.input.capella.get("test_users"))
        else:
            self.test_users = {"User": {"password": self.passwd, "mailid": self.user,
                                        "role": "organizationOwner"}}
        self.capi = CapellaAPI(
            "https://" + self.url,
            self.secret_key,
            self.access_key,
            self.user,
            self.passwd
        )

        self.sso = SSOComponents(self.capi, "https://" + self.url)

        self.setup_sso()

    def setup_sso(self):
        tenants = self.sso.get_teams(self.tenant_id)

        # We only care about the most significant digit in this case 2xx being ok
        # anything else being considered an error in the test.
        self.assertEqual(tenants.status_code // 100, 2)

        # Do some decoding
        data = json.loads(tenants.content)
        self.assertEqual(len(data['data']), 1)

        # This is safe due to the above assertion.
        team = data['data'][0]['data']
        self.assertIsNotNone(team['id'])
        self.log.info("Got Team ID: {}".format(team['id']))

        realm = self.sso.create_realm(tenant_id=self.tenant_id, team_id=team['id'])
        self.assertEqual(realm.status_code // 100, 2, realm.content)
        realm = json.loads(realm.content)
        self.assertIsNotNone(realm['realmName'])

        name = realm['realmName']
        realm_content = self.sso.get_realm_by_name(self.tenant_id, name)
        self.assertIsNotNone(realm_content['id'])

        self.realm_id = realm_content['id']
        self.realm_callback = realm_content['identityProviderConnection']['settings']['callbackURL']
        self.realm_entity = realm_content['identityProviderConnection']['settings']['entityId']
        self.realm_name = realm['realmName']

    def tearDown(self):
        self.log.info("Destroying Test Realm")
        super(SSOTest, self).tearDown()
        drealm = self.sso.delete_realm(self.tenant_id, self.realm_id)
        self.log.info(drealm.headers)
        self.assertEqual(drealm.status_code // 100, 2, drealm.content)

    def test_lifecycle_realm(self):
        # For this test we just want to run the setup and teardown
        self.log.info("Lifecycle Realm")

    def test_login_with_unsigned_response(self):
        self.log.info("Login with Unsigned response")

        # This code is different from the rest of the lifecycle. This is the code
        # that will go and perform the SAML attestation.
        login_flow = self.sso.initiate_idp_login(self.realm_name)
        self.assertEqual(login_flow.status_code // 100, 2)
        login_flow = json.loads(login_flow.content)
        self.log.info("Got Login Flow: {}".format(login_flow['loginURL']))

        # Get the SAML Request
        saml_request = self.sso.get_saml_request(login_flow['loginURL'])
        self.assertEqual(saml_request.status_code // 100, 2)
        c = saml_request.cookies

        saml_request_dict = self.sso.parse_saml_request(saml_request.content)
        self.log.info(saml_request_dict)
        self.assertIsNotNone(saml_request_dict["SAMLRequest"])
        self.assertIsNotNone(saml_request_dict["RelayState"])

        identifier = self.sso.decode_saml_request(saml_request_dict['SAMLRequest'])
        self.log.info("Got Request ID: {}".format(identifier))

        s = SAMLResponse(requestId=identifier, spname=self.realm_entity, acs=self.realm_callback)
        s.generateRoot()
        s.subject("test-user1")
        s.attribute("uid", ["test-user1"])
        s.attribute("mail", ["test-user1@capella.test"])

        response = s.to_base64()
        login_response = self.sso.send_saml_response(
            self.realm_callback,
            response,
            saml_request_dict["RelayState"],
            cookies=c
        )

        self.assertEqual(login_response.status_code // 100, 3)

        continue_flow = self.sso.continue_saml_response(
            urljoin(
                self.realm_callback,
                login_response.headers['Location']
            ),
            cookies=c
        )

        self.assertEqual(continue_flow.status_code // 100, 3)

        new_url = urlparse(continue_flow.headers['Location'])
        new_url = "https://{}/v2/auth{}?{}".format(self.url.replace("cloud", "", 1), new_url.path, new_url.query)
        finish_flow = self.sso.continue_saml_response(new_url)

        self.assertNotEqual(finish_flow.status_code // 100, 2, finish_flow.content)

    def test_login_with_invalid_signature(self):
        self.log.info("Login with SSO")

        # This code is different from the rest of the lifecycle. This is the code
        # that will go and perform the SAML attestation.
        login_flow = self.sso.initiate_idp_login(self.realm_name)
        self.assertEqual(login_flow.status_code // 100, 2)
        login_flow = json.loads(login_flow.content)
        self.log.info("Got Login Flow: {}".format(login_flow['loginURL']))

        # Get the SAML Request
        saml_request = self.sso.get_saml_request(login_flow['loginURL'])
        self.assertEqual(saml_request.status_code // 100, 2)
        c = saml_request.cookies

        saml_request_dict = self.sso.parse_saml_request(saml_request.content)
        self.log.info(saml_request_dict)
        self.assertIsNotNone(saml_request_dict["SAMLRequest"])
        self.assertIsNotNone(saml_request_dict["RelayState"])

        id = self.sso.decode_saml_request(saml_request_dict['SAMLRequest'])
        self.log.info("Got Request ID: {}".format(id))

        s = SAMLResponse(requestId=id, spname=self.realm_entity, acs=self.realm_callback)
        s.generateRoot()
        s.subject("test-user1")
        s.attribute("uid", ["test-user1"])
        s.attribute("mail", ["test-user1@capella.test"])

        self.log.info(s.to_string())

        ss = SAMLSignatory()
        dgst = ss.digest(s.to_string())
        s.add_digest(dgst, self.sso.get_certificate())
        sig = self.sso.sign(ss.digest(s.signed_info_to_string()))
        s.add_signature(sig)

        self.log.info("Digest: {}".format(dgst))
        self.log.info("Signature: {}".format(sig))

        response = s.to_base64()
        self.log.info(response)
        login_response = self.sso.send_saml_response(self.realm_callback, response, saml_request_dict["RelayState"],
                                                     cookies=c)

        self.assertEqual(login_response.status_code // 100, 3)

        loc = urlparse(login_response.headers['Location'])
        self.log.info(self.url.replace('cloud', '', 1))
        self.log.info(loc.path+'?'+loc.query)
        self.log.info(urljoin('https://' + self.url.replace('cloud', '', 1), '/v2/auth' + loc.path+'?'+loc.query))

        continue_flow = self.sso.continue_saml_response(
            urljoin('https://' + self.url.replace('cloud', '', 1), '/v2/auth' + loc.path+'?'+loc.query), cookies=c)

        self.assertEqual(continue_flow.status_code // 100, 3)

        new_url = urlparse(continue_flow.headers['Location'])
        new_url = "https://{}/v2/auth{}?{}".format(self.url.replace("cloud", "", 1), new_url.path, new_url.query)
        finish_flow = self.sso.continue_saml_response(new_url)

        self.log.info(finish_flow.headers)
        self.log.info(finish_flow.content)

        self.assertNotEqual(finish_flow.status_code // 100, 2, finish_flow.content)

