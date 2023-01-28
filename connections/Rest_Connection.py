"""
Created on Sep 25, 2017

@author: riteshagarwal
"""
import base64
import json
import traceback
import socket
import time

from Cb_constants import constants, CbServer
from TestInput import TestInputSingleton
from common_lib import sleep
from global_vars import logger
from membase.api import httplib2
from custom_exceptions.exception import ServerUnavailableException

import requests

try:
    requests.packages.urllib3.disable_warnings()
except:
    pass
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class RestConnection(object):
    DELETE = "DELETE"
    GET = "GET"
    POST = "POST"
    PUT = "PUT"

    def __new__(self, serverInfo={}, node=None):
        # allow port to determine
        # behavior of rest connection
        self.log = logger.get("infra")
        port = None
        if isinstance(serverInfo, dict):
            if 'port' in serverInfo:
                port = serverInfo['port']
        else:
            port = serverInfo.port

        if not port:
            port = constants.port

        if int(port) in xrange(9091, 9100):
            # return elastic search rest connection
            from membase.api.esrest_client import EsRestConnection
            obj = object.__new__(EsRestConnection, serverInfo)
        else:
            # default
            obj = object.__new__(self, serverInfo)
        return obj

    def __init__(self, serverInfo, timeout=300):
        # serverInfo can be a json object/dictionary
        index_port = constants.index_port
        fts_port = constants.fts_port
        query_port = constants.n1ql_port
        eventing_port = constants.eventing_port
        backup_port = constants.backup_port
        if isinstance(serverInfo, dict):
            self.ip = serverInfo["ip"]
            self.username = serverInfo["username"]
            self.password = serverInfo["password"]
            port = serverInfo["port"]
            if "index_port" in serverInfo.keys():
                index_port = serverInfo["index_port"]
            if "fts_port" in serverInfo.keys():
                if serverInfo['fts_port']:
                    fts_port = serverInfo["fts_port"]
            if "eventing_port" in serverInfo.keys():
                if serverInfo['eventing_port']:
                    self.eventing_port = serverInfo["eventing_port"]
            self.hostname = ''
            self.services = ''
            if "hostname" in serverInfo:
                self.hostname = serverInfo["hostname"]
            if "services" in serverInfo:
                self.services = serverInfo["services"]
        else:
            self.ip = serverInfo.ip
            self.username = serverInfo.rest_username
            self.password = serverInfo.rest_password
            self.port = serverInfo.port
            self.hostname = ''
            self.services = "kv"
            if hasattr(serverInfo, "services"):
                self.services = serverInfo.services
            if hasattr(serverInfo, 'index_port') \
                    and serverInfo.index_port:
                index_port = serverInfo.index_port
            if hasattr(serverInfo, 'query_port') \
                    and serverInfo.query_port:
                query_port = serverInfo.query_port
            if hasattr(serverInfo, 'fts_port') \
                    and serverInfo.fts_port:
                fts_port = serverInfo.fts_port
            if hasattr(serverInfo, 'eventing_port') \
                    and serverInfo.eventing_port:
                self.eventing_port = serverInfo.eventing_port
            if hasattr(serverInfo, 'hostname') and serverInfo.hostname \
                    and serverInfo.hostname.find(self.ip) == -1:
                self.hostname = serverInfo.hostname
            if hasattr(serverInfo, 'services'):
                self.services = serverInfo.services
        if CbServer.use_https:
            self.port = CbServer.ssl_port
            index_port = CbServer.ssl_index_port
            query_port = CbServer.ssl_n1ql_port
            fts_port = CbServer.ssl_fts_port
            eventing_port = CbServer.ssl_eventing_port
        self.input = TestInputSingleton.input
        if self.input is not None:
            """ from watson, services param order and format:
                new_services=fts-kv-index-n1ql """
            self.services_node_init = self.input.param("new_services", None)

        http_url = "http://%s:%s/"
        https_url = "https://%s:%s/"
        generic_url = http_url
        if CbServer.use_https:
            generic_url = https_url
        url_host = "%s" % self.ip
        if self.hostname:
            url_host = "%s" % self.hostname

        self.baseUrl = generic_url % (url_host, self.port)
        self.indexUrl = generic_url % (url_host, index_port)
        self.queryUrl = generic_url % (url_host, query_port)
        self.ftsUrl = generic_url % (url_host, fts_port)
        self.eventing_baseUrl = generic_url % (url_host, eventing_port)
        self.backup_url = generic_url % (url_host, backup_port)

        # for Node is unknown to this cluster error
        node_unknown_msg = "Node is unknown to this cluster"
        unexpected_server_err_msg = "Unexpected server error, request logged"
        for iteration in xrange(5):
            http_res, success = \
                self.init_http_request(self.baseUrl + 'nodes/self', timeout)
            if not success and type(http_res) == unicode \
                    and (http_res.find(node_unknown_msg) > -1
                         or http_res.find(unexpected_server_err_msg) > -1):
                self.log.error("Error {0}, 5 seconds sleep before retry"
                               .format(http_res))
                sleep(5, log_type="infra")
                if iteration == 2:
                    self.log.error("Node {0}:{1} is in a broken state!"
                                   .format(self.ip, self.port))
                    raise ServerUnavailableException(self.ip)
                continue
            else:
                break

    def init_http_request(self, api, timeout=300):
        content = None
        try:
            status, content, header = self._http_request(
                api, 'GET', headers=self._create_capi_headers(), timeout=timeout)
            json_parsed = json.loads(content)
            if status:
                return json_parsed, True
            else:
                print("{0} with status {1}: {2}"
                      .format(api, status, json_parsed))
                return json_parsed, False
        except ValueError as e:
            if content is not None:
                print("{0}: {1}".format(api, content))
            else:
                print(e)
            return content, False

    def _create_capi_headers(self, username=None, password=None, contentType='application/json', connection='close'):
        if username is None:
            username = self.username
        if password is None:
            password = self.password
        authorization = base64.encodestring('%s:%s' % (username, password)).strip("\n")
        return {'Content-Type': contentType,
                'Authorization': 'Basic %s' % authorization,
                'Connection': connection,
                'Accept': '*/*'}

    @staticmethod
    def get_auth(headers):
        key = 'Authorization'
        if key in headers:
            val = headers[key]
            if val.startswith("Basic "):
                return "auth: " + base64.decodestring(val[6:])
        return ""

    def _urllib_request(self, api, method='GET', params='', headers=None,
                        timeout=300, verify=False, session=None):
        if session is None:
            session = requests.Session()
        end_time = time.time() + timeout
        while True:
            try:
                if method == "GET":
                    response = session.get(api, params=params, headers=headers,
                                           timeout=timeout, verify=verify)
                elif method == "POST":
                    response = session.post(api, data=params, headers=headers,
                                            timeout=timeout, verify=verify)
                elif method == "DELETE":
                    response = session.delete(api, data=params, headers=headers,
                                              timeout=timeout, verify=verify)
                elif method == "PUT":
                    response = session.put(api, data=params, headers=headers,
                                           timeout=timeout, verify=verify)
                elif method == "PATCH":
                    response = session.patch(api, data=params, headers=headers,
                                             timeout=timeout, verify=verify)
                status = response.status_code
                content = response.content
                if status in [200, 201, 202, 204]:
                    return True, content, response
                else:
                    try:
                        json_parsed = json.loads(content)
                    except ValueError:
                        json_parsed = dict()
                        json_parsed["error"] = "status: {0}, content: {1}".format(
                            response['status'], content)
                    reason = "unknown"
                    if "error" in json_parsed:
                        reason = json_parsed["error"]
                    elif "errors" in json_parsed:
                        reason = json_parsed["errors"]
                    if ("accesskey" in params.lower()) or (
                            "secretaccesskey" in params.lower()) or (
                            "password" in params.lower()) or (
                            "secretkey" in params.lower()):
                        message = '{0} {1} body: {2} headers: {3} ' \
                                  'error: {4} reason: {5} {6} {7}'. \
                            format(method, api,
                                   "Body is being redacted because it contains sensitive info",
                                   headers, response.status_code, reason,
                                   content.rstrip('\n'),
                                   RestConnection.get_auth(headers))
                    else:
                        message = '{0} {1} body: {2} headers: {3} ' \
                                  'error: {4} reason: {5} {6} {7}'. \
                            format(method, api, params, headers,
                                   response.status_code, reason,
                                   content.rstrip('\n'),
                                   RestConnection.get_auth(headers))
                    self.log.error(message)
                    self.log.debug(''.join(traceback.format_stack()))
                    return False, content, response
            except requests.exceptions.HTTPError as errh:
                self.log.error("HTTP Error {0}".format(errh))
            except requests.exceptions.ConnectionError as errc:
                if "Illegal state exception" in str(errc):
                    # Known ssl bug, retry
                    pass
                else:
                    self.log.error("Error Connecting {0}".format(errc))
                if time.time() > end_time:
                    raise ServerUnavailableException(ip=self.ip)
            except requests.exceptions.Timeout as errt:
                self.log.error("Timeout Error: {0}".format(errt))
                if time.time() > end_time:
                    raise ServerUnavailableException(ip=self.ip)
            except requests.exceptions.RequestException as err:
                self.log.error("Something else: {0}".format(err))
                if time.time() > end_time:
                    raise ServerUnavailableException(ip=self.ip)
            sleep(3, log_type="infra")

    def _http_request(self, api, method='GET', params='', headers=None,
                      timeout=300):
        if not headers:
            headers = self._create_headers()
        if CbServer.use_https:
            status, content, response = \
                self._urllib_request(api, method=method, params=params, headers=headers,
                                     timeout=timeout, verify=False)
            return status, content, response
        end_time = time.time() + timeout
        while True:
            try:
                response, content = httplib2.Http(timeout=timeout).request(
                    api, method, params, headers)
                if response.status in [200, 201, 202, 204]:
                    return True, content, response
                else:
                    try:
                        json_parsed = json.loads(content)
                    except ValueError:
                        json_parsed = dict()
                        json_parsed["error"] = "status: {0}, content: {1}" \
                            .format(response['status'], content)
                    reason = "unknown"
                    if "error" in json_parsed:
                        reason = json_parsed["error"]
                    if ("accesskey" in params.lower()) or ("secretaccesskey" in params.lower()) or (
                            "password" in params.lower()) or ("secretkey" in params.lower()):
                        message = '{0} {1} body: {2} headers: {3} ' \
                                  'error: {4} reason: {5} {6} {7}'. \
                            format(method, api, "Body is being redacted because it contains sensitive info", headers,
                                   response['status'], reason,
                                   content.rstrip('\n'),
                                   RestConnection.get_auth(headers))
                    else:
                        message = '{0} {1} body: {2} headers: {3} ' \
                                  'error: {4} reason: {5} {6} {7}'. \
                            format(method, api, params, headers,
                                   response['status'], reason,
                                   content.rstrip('\n'),
                                   RestConnection.get_auth(headers))
                    self.log.error(message)
                    self.log.debug(''.join(traceback.format_stack()))
                    return False, content, response
            except socket.error as e:
                self.log.error("Socket error while connecting to {0}. "
                               "Error {1}".format(api, e))
                if time.time() > end_time:
                    raise ServerUnavailableException(ip=self.ip)
            except httplib2.ServerNotFoundError as e:
                self.log.error("ServerNotFoundError while connecting to {0}. "
                               "Error {1}".format(api, e))
                if time.time() > end_time:
                    raise ServerUnavailableException(ip=self.ip)
            sleep(3, log_type="infra")

    def _create_headers(self, username=None, password=None):
        if username is None:
            username = self.username
        if password is None:
            password = self.password
        authorization = base64.encodestring('%s:%s'
                                            % (username, password)).strip("\n")
        return {'Content-Type': 'application/x-www-form-urlencoded',
                'Authorization': 'Basic %s' % authorization,
                'Connection': 'close',
                'Accept': '*/*'}

    def get_headers_for_content_type_json(self):
        authorization = base64.encodestring('%s:%s'
                                            % (self.username, self.password)).strip("\n")
        return {'Content-type': 'application/json',
                'Authorization': 'Basic %s' % authorization}
