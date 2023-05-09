import json
import time
import requests

from Cb_constants import CbServer, DocLoading
from common_lib import IDENTIFIER_TOKEN
from global_vars import logger
from TestInput import TestInputSingleton


def check_sirius_status(base_url):
    exception = None
    for i in range(5):
        try:
            response = requests.get(url=base_url + "/check-online")
            if response.status_code != 200:
                return False
            print("Sirius is  online")
            return True
        except Exception as e:
            exception = e

    raise (exception)


def clear_test_information(base_url, identifier_token, username, password, bucket, scope=CbServer.default_scope,
                           collection=CbServer.default_collection):
    if not check_sirius_status(base_url):
        raise Exception("sirius is not online")
    headers = {
        'Content-Type': 'application/json',
    }
    json_data_request = {
        'username': username,
        'password': password,
        'identifierToken': identifier_token,
        'bucket': bucket,
        'scope': scope,
        'collection': collection,
    }

    exception = None
    for i in range(5):
        try:
            path = "/clear_data"
            response = requests.post(url=base_url + path, headers=headers, json=json_data_request)
            response_data = json.loads(response.content)
            print("cleaning", response_data)
            if not response_data["error"]:
                print(response_data)
            return
        except Exception as e:
            print(str(e))
            exception = e

    raise exception


class RESTClient(object):

    def __init__(self, servers, bucket,
                 scope=CbServer.default_scope,
                 collection=CbServer.default_collection,
                 username="Administrator", password="password",
                 compression_settings=None, sirius_base_url="http://0.0.0.0:80"):
        """
        :param servers: List of servers for SDK to establish initial
                        connections with
        :param bucket: Bucket object to which the SDK connection will happen
        :param scope:  Name of the scope to connect.
                       Default: '_default'
        :param collection: Name of the collection to connect.
                           Default: _default
        :param username: User name using which to establish the connection
        :param password: Password for username authentication
        :param compression_settings: Dict of compression settings. Format:
                                     {
                                      "enabled": Bool,
                                      "minRatio": Double int (None to default),
                                      "minSize": int (None to default)
                                     }
        :param cert_path: Path of certificate file to establish connection
        """
        input = TestInputSingleton.input
        self.sirius_base_url = sirius_base_url
        self.sirius_base_url = input.param("sirius_url", self.sirius_base_url)

        self.servers = list()
        self.hosts = list()
        self.scope_name = scope
        self.collection_name = collection
        self.username = username
        self.password = password

        self.default_timeout = 0
        self.cluster = None
        self.bucket = bucket
        self.bucketObj = None
        self.collection = None
        self.compression = compression_settings

        self.cluster_config = None
        self.timeouts_config = None
        self.compression_config = None
        self.connection_string = ""
        self.retry = None
        self.retry_interval = None
        self.delete_record = None
        self.op_type = None

        self.log = logger.get("test")
        for server in servers:
            self.servers.append((server.ip, int(server.port)))
            if server.ip == "127.0.0.1":
                if CbServer.use_https:
                    self.scheme = "https"
                else:
                    self.scheme = "http"
            else:
                self.hosts.append(server.ip)
                if CbServer.use_https:
                    self.scheme = "couchbases"
                else:
                    self.scheme = "couchbase"

        if not check_sirius_status(self.sirius_base_url):
            raise Exception("sirius not online")

        self.create_cluster_config()

    def create_cluster_config(self):
        self.log.debug("Creating Cluster Config connection for '%s'" % self.bucket)

        self.connection_string = "{0}://{1}".format(self.scheme, ", ".
                                                    join(self.hosts).
                                                    replace(" ", ""))

        self.cluster_config = self.create_payload_cluster_config()

    def send_request(self, path, method, payload=None):
        headers = {
            "Content-Type": "application/json"
        }

        url = self.sirius_base_url + path
        print(url, payload)

        exception = None
        for i in range(5):
            try:
                response = requests.post(url, headers=headers, json=payload)
                return response
            except Exception as e:
                print(str(e))
                exception = e

        if exception is not None:
            raise exception

    def create_payload_cluster_config(self):
        self.compression_config = self.create_payload_compression_config()
        self.timeouts_config = self.create_payload_timeouts_config()
        payload = {
            "clusterConfig": {
                "username": self.username,
                "password": self.password,
                "connectionString": self.connection_string
            }
        }

        if self.compression_config:
            payload["clusterConfig"].update(self.compression_config)

        if self.timeouts_config:
            payload["clusterConfig"].update(self.timeouts_config)

        return payload

    def create_payload_compression_config(self):
        payload = {
            "compressionConfig": {}
        }
        if type(self.compression) == dict and "minSize" in self.compression and "minRatio" in self.compression:
            payload["compressionConfig"]["disabled"] = self.compression["enabled"]

            payload["compressionConfig"]["minSize"] = self.compression_config["minSize"]

            payload["compressionConfig"]["minRatio"] = self.compression_config["minRatio"]

        return payload

    def create_payload_timeouts_config(self, connect_timeout=20, kv_timeout=10, kv_durable_timeout=None):
        payload = {
            "timeoutsConfig": {}
        }

        if connect_timeout is not None:
            payload["timeoutsConfig"]["connectTimeout"] = connect_timeout

        if kv_timeout is not None:
            payload["timeoutsConfig"]["KVTimeout"] = kv_timeout

        if kv_durable_timeout is not None:
            payload["timeoutsConfig"]["KVDurableTimeout"] = kv_durable_timeout

        return payload

    def create_payload_insert_options(self, expiry=None, persist_to=None, replicate_to=None, durability=None,
                                      timeout=None):
        payload = {
            "insertOptions": {}
        }

        if expiry is not None:
            payload["insertOptions"]["expiry"] = expiry

        if persist_to is not None:
            payload["insertOptions"]["persistTo"] = persist_to

        if replicate_to is not None:
            payload["insertOptions"]["replicateTo"] = replicate_to

        if durability is not None:
            payload["insertOptions"]["durability"] = durability

        if timeout is not None:
            payload["insertOptions"]["timeout"] = timeout

        return payload

    def create_payload_remove_options(self, cas=None, persist_to=None, replicate_to=None, durability=None,
                                      timeout=None):
        payload = {
            "removeOptions": {}
        }

        if cas is not None:
            payload["removeOptions"]["cas"] = cas

        if persist_to is not None:
            payload["removeOptions"]["persistTo"] = persist_to

        if replicate_to is not None:
            payload["removeOptions"]["replicateTo"] = replicate_to

        if durability is not None:
            payload["removeOptions"]["durability"] = durability

        if timeout is not None:
            payload["removeOptions"]["timeout"] = timeout

        return payload

    def create_payload_replace_options(self, expiry=None, cas=None, persist_to=None, replicate_to=None,
                                       durability=None, timeout=None):
        payload = {
            "replaceOptions": {}
        }

        if expiry is not None:
            payload["replaceOptions"]["expiry"] = expiry

        if cas is not None:
            payload["replaceOptions"]["cas"] = cas

        if persist_to is not None:
            payload["replaceOptions"]["persistTo"] = persist_to

        if replicate_to is not None:
            payload["replaceOptions"]["replicateTo"] = replicate_to

        if durability is not None:
            payload["replaceOptions"]["durability"] = durability

        if timeout is not None:
            payload["replaceOptions"]["timeout"] = timeout

        return payload

    def create_payload_key_value(self, key, value):
        payload = {
            "key": key,
            "value": value
        }
        return payload

    def create_payload_operation_config(self, count=None, doc_size=1024, doc_type="json", key_size="100", key_prefix="",
                                        key_suffix="", random_doc_size=False, random_key_size=False,
                                        read_your_own_write=False, template_name="Person", start=0, end=0,
                                        fields_to_change=[]):
        payload = {
            "operationConfig": {
                "docSize": doc_size,
                "keyPrefix": key_prefix,
                "keySuffix": key_suffix,
                "template": template_name,
                "start": start,
                "end": end
            }
        }

        if count is not None:
            payload["operationConfig"]["count"] = count

        if doc_type is not None:
            payload["operationConfig"]["docType"] = doc_type

        if key_size is not None:
            payload["operationConfig"]["keySize"] = key_size

        if random_doc_size is not None:
            payload["operationConfig"]["randomDocSize"] = random_doc_size

        if random_key_size is not None:
            payload["operationConfig"]["randomKeySize"] = random_key_size

        if read_your_own_write is not None:
            payload["operationConfig"]["readYourOwnWrite"] = read_your_own_write

        if fields_to_change is not None:
            payload["operationConfig"]["fieldsToChange"] = fields_to_change

        return payload

    def create_payload_single_operation_config(self, key, value=None, read_your_own_write=False):

        payload = {
            "singleOperationConfig": {
                "keyValue": [self.create_payload_key_value(key, value)]
            }
        }

        if read_your_own_write:
            payload["singleOperationConfig"]["readYourOwnWrite"] = read_your_own_write

        return payload

    def get_result_using_seed(self, response, load_type="bulk"):
        fail = {}
        if response.status_code != 200:
            print("unable to retrieve the result of operation :" + self.op_type + " " + str(response.status_code))
            raise Exception("Bad HTTP status at doc loading")

        result_from_bulk_request = json.loads(response.content)
        request_using_seed = {}
        result_using_seed = {}

        if not result_from_bulk_request["error"]:
            request_using_seed = {
                "seed": result_from_bulk_request["data"]["seed"],
                "deleteRecord": self.delete_record,
            }
        else:
            print("error in initiating task")
            raise Exception("Bad/Malformed operation request")

        for i in range(0, self.retry):
            response = self.send_request("/result", "GET", request_using_seed)
            result_using_seed = json.loads(response.content)
            if not result_using_seed["error"]:
                break
            time.sleep(self.retry_interval * 60)

        if response.status_code != 200:
            print("unable to retrieve the result of operation :" + self.op_type + " " + str(response.status_code))
            raise Exception("Bad HTTP status at fetching result")

        if not result_using_seed["error"]:
            if "otherErrors" in result_using_seed["data"].keys() and result_using_seed["data"]["otherErrors"] != "":
                raise Exception(result_using_seed["data"]["otherErrors"])
            else:
                if type == "bulk":
                    for error_name, failed_documents in result_using_seed["data"]["bulkErrors"].items():
                        for kv in failed_documents:
                            key = kv["key"]
                            value = kv["value"]
                            error_string = kv["errorString"]
                            fail[key] = dict()
                            fail[key]['value'] = value
                            fail[key]['error'] = error_string
                    return fail, result_using_seed["data"]["success"], result_using_seed["data"]["failure"]
                else:
                    return result_using_seed["data"]["singleResult"], result_using_seed["data"]["success"], \
                        result_using_seed["data"]["failure"]
        else:
            raise Exception(result_using_seed["message"])

    def do_bulk_operation(self, op_type, identifier_token, insert_options=None, remove_options=None,
                          operation_config=None, retry=10, retry_interval=1, delete_record=False):

        self.retry = retry
        self.retry_interval = retry_interval
        self.delete_record = delete_record
        self.op_type = op_type

        payload = {"identifierToken": identifier_token, "bucket": self.bucket.name, "scope": self.scope_name,
                   "collection": self.collection_name}

        payload.update(self.cluster_config)

        if operation_config is not None:
            payload.update(operation_config)

        path = ""
        method = "POST"

        if op_type == "create":
            path = "/bulk-create"
            if insert_options is not None:
                payload.update(insert_options)

        elif op_type == "update":
            path = "/bulk-upsert"
            if insert_options is not None:
                payload.update(insert_options)

        elif op_type == "delete":
            path = "/bulk-delete"
            if remove_options is not None:
                payload.update(remove_options)

        elif op_type == "read":
            path = "/bulk-read"

        elif self.op_type == "validate":
            path = "/validate"

        else:
            self.log.debug("unknown rest doc loading operation")
            raise Exception("unknown rest doc loading operation")

        response = self.send_request(path=path, method=method, payload=payload)
        return self.get_result_using_seed(response, load_type="bulk")

    # op_type - "create", "read", "update", or "delete" (String)
    def do_single_operation(self, op_type, identifier_token, insert_options=None, remove_options=None,
                            replace_options=None, single_operation_config=None, retry=1000, retry_interval=0.2,
                            delete_record=False):

        self.retry = retry
        self.retry_interval = retry_interval
        self.delete_record = delete_record
        self.op_type = op_type

        payload = {"identifierToken": identifier_token, "bucket": self.bucket.name, "scope": self.scope_name,
                   "collection": self.collection_name}

        payload.update(self.cluster_config)

        if single_operation_config is not None:
            payload.update(single_operation_config)

        path = ""
        method = "POST"

        if op_type == DocLoading.Bucket.DocOps.CREATE:
            path = "/single-create"
            if insert_options is not None:
                payload.update(insert_options)

        elif op_type == DocLoading.Bucket.DocOps.UPDATE:
            path = "/single-upsert"
            if insert_options is not None:
                payload.update(insert_options)

        elif op_type == DocLoading.Bucket.DocOps.DELETE:
            path = "/single-delete"
            if remove_options is not None:
                payload.update(remove_options)

        elif op_type == DocLoading.Bucket.DocOps.READ:
            path = "/single-read"

        elif op_type == DocLoading.Bucket.DocOps.REPLACE:
            path = "/single-replace"
            if replace_options is not None:
                payload.update(replace_options)

        elif op_type == DocLoading.Bucket.DocOps.TOUCH:
            path = "/single-touch"
            if insert_options is not None:
                payload.update(insert_options)

        else:
            self.log.debug("unknown rest doc loading operation")
            raise Exception("unknown rest doc loading operation")

        response = self.send_request(path=path, method=method, payload=payload)
        return self.get_result_using_seed(response, load_type="single")

    def translate_to_json_object(value, doc_type="json"):
        # if type(value) == JsonObject and doc_type == "json":
        #     return value
        # json_obj = JsonObject.create()
        # try:
        #     if doc_type.find("json") != -1:
        #         if type(value) != dict:
        #             value = pyJson.loads(value)
        #         for field, val in value.items():
        #             json_obj.put(field, val)
        #         return json_obj
        #     elif doc_type.find("binary") != -1:
        #         value = String(value)
        #         return value.getBytes(StandardCharsets.UTF_8)
        #     else:
        #         return value
        # except Exception:
        #     pass
        # return json_obj
        return value

    def replace(self, key, value,
                exp=0, exp_unit="seconds",
                persist_to=0, replicate_to=0,
                timeout=5, time_unit="seconds",
                durability="", cas=0, sdk_retry_strategy=None,
                preserve_expiry=None, identifier_token=IDENTIFIER_TOKEN):
        result = dict()
        result["cas"] = 0
        # content = self.translate_to_json_object(value)

        try:
            replace_options = self.create_payload_replace_options(expiry=exp, cas=cas, persist_to=persist_to,
                                                                  replicate_to=replicate_to,
                                                                  durability=durability, timeout=timeout)
            single_operation_config = self.create_payload_single_operation_config(key=key, value=value)
            data, _, failure_count = self.do_single_operation(DocLoading.Bucket.DocOps.REPLACE,
                                                              identifier_token=identifier_token,
                                                              replace_options=replace_options,
                                                              single_operation_config=single_operation_config)
            if failure_count > 0:
                result.update({"key": key, "value": value, "error": data[key]["errorString"], "status": False,
                               "cas": 0})
            else:
                result.update({"key": key, "value": value,
                               "status": True, "cas": data[key]["cas"]})

        except Exception as e:
            self.log.error("Something else happened: " + str(e))
            result.update({"key": key, "value": None,
                           "error": str(e), "cas": 0, "status": False})
        return result

    def touch(self, key, exp=0, exp_unit="seconds",
              persist_to=0, replicate_to=0,
              durability="",
              timeout=5, time_unit="seconds",
              sdk_retry_strategy=None, identifier_token=IDENTIFIER_TOKEN):

        result = {}

        try:
            insert_options = self.create_payload_insert_options(expiry=exp, persist_to=persist_to,
                                                                replicate_to=replicate_to,
                                                                durability=durability, timeout=timeout)

            single_operation_config = self.create_payload_single_operation_config(key=key)
            data, _, failure_count = self.do_single_operation(DocLoading.Bucket.DocOps.TOUCH,
                                                              identifier_token=identifier_token,
                                                              insert_options=insert_options,
                                                              single_operation_config=single_operation_config)
            if failure_count > 0:
                result.update({"key": key, "value": None, "error": data[key]["errorString"], "status": False,
                               "cas": 0})
            else:
                result.update({"key": key,
                               "status": True, "value": None, "cas": data[key]["cas"]})

        except Exception as e:
            self.log.error("Something else happened: " + str(e))
            result.update({"key": key, "value": None,
                           "error": str(e), "cas": 0, "status": False})
            return result

    def read(self, key, timeout=5, time_unit="seconds",
             sdk_retry_strategy=None, access_deleted=False, identifier_token=IDENTIFIER_TOKEN):
        result = {}
        try:

            single_operation_config = self.create_payload_single_operation_config(key=key)
            data, _, failure_count = self.do_single_operation(DocLoading.Bucket.DocOps.READ,
                                                              identifier_token=identifier_token,
                                                              single_operation_config=single_operation_config)
            if failure_count > 0:
                result.update({"key": key, "value": None,
                               "error": data[key]["errorString"], "status": False,
                               "cas": 0})
            else:
                result.update({"key": key, "value": None,
                               "status": True, "cas": data[key]["cas"]})

        except Exception as e:
            self.log.error("Something else happened: " + str(e))
            result.update({"key": key, "value": None,
                           "error": str(e), "cas": 0, "status": False})
        return result

    def upsert(self, key, value,
               exp=0, exp_unit="seconds",
               persist_to=0, replicate_to=0,
               timeout=5, time_unit="seconds",
               durability="", sdk_retry_strategy=None, preserve_expiry=None, identifier_token=IDENTIFIER_TOKEN):
        # content = self.translate_to_json_object(value)
        result = dict()
        result["cas"] = 0
        try:
            insert_options = self.create_payload_insert_options(expiry=exp, persist_to=persist_to,
                                                                replicate_to=replicate_to,
                                                                durability=durability, timeout=timeout)
            single_operation_config = self.create_payload_single_operation_config(key=key, value=value)
            data, _, failure_count = self.do_single_operation(DocLoading.Bucket.DocOps.UPDATE,
                                                              identifier_token=identifier_token,
                                                              insert_options=insert_options,
                                                              single_operation_config=single_operation_config)
            if failure_count > 0:
                result.update({"key": key, "value": value,
                               "error": data[key]["errorString"], "status": False,
                               "cas": 0})
            else:
                result.update({"key": key, "value": value,
                               "status": True, "cas": data[key]["cas"]})
        except Exception as e:
            self.log.error("Something else happened: " + str(e))
            result.update({"key": key, "value": None,
                           "error": str(e), "cas": 0, "status": False})
        return result

    def insert(self, key, value,
               exp=0, exp_unit="seconds",
               persist_to=0, replicate_to=0,
               timeout=5, time_unit="seconds",
               durability="", sdk_retry_strategy=None, preserve_expiry=None, identifier_token=IDENTIFIER_TOKEN):
        # content = self.translate_to_json_object(value)
        result = dict()
        result["cas"] = 0
        try:
            insert_options = self.create_payload_insert_options(expiry=exp, persist_to=persist_to,
                                                                replicate_to=replicate_to,
                                                                durability=durability, timeout=timeout)
            single_operation_config = self.create_payload_single_operation_config(key=key, value=value)
            data, _, failure_count = self.do_single_operation(DocLoading.Bucket.DocOps.CREATE,
                                                              identifier_token=identifier_token,
                                                              insert_options=insert_options,
                                                              single_operation_config=single_operation_config)
            if failure_count > 0:
                result.update({"key": key, "value": value,
                               "error": data[key]["errorString"], "status": False,
                               "cas": 0})
            else:
                result.update({"key": key, "value": value,
                               "status": True, "cas": data[key]["cas"]})

        except Exception as e:
            self.log.error("Something else happened: " + str(e))
            result.update({"key": key, "value": None,
                           "error": str(e), "cas": 0, "status": False})
        return result

    def delete(self, key, persist_to=0, replicate_to=0,
               timeout=5, time_unit="seconds",
               durability="", cas=0, sdk_retry_strategy=None, identifier_token=IDENTIFIER_TOKEN):
        result = dict()
        result["cas"] = -1
        result = dict()
        try:
            remove_options = self.create_payload_remove_options(cas=cas, persist_to=persist_to,
                                                                replicate_to=replicate_to,
                                                                durability=durability, timeout=timeout)
            single_operation_config = self.create_payload_single_operation_config(key=key)
            data, _, failure_count = self.do_single_operation(DocLoading.Bucket.DocOps.DELETE,
                                                              identifier_token=identifier_token,
                                                              remove_options=remove_options,
                                                              single_operation_config=single_operation_config)
            if failure_count > 0:
                result.update({"key": key, "error": data[key]["errorString"], "status": False,
                               "cas": 0})
            else:
                result.update({"key": key, "status": True, "cas": data[key]["cas"]})

        except Exception as e:
            self.log.error("Something else happened: " + str(e))
            result.update({"key": key, "value": None,
                           "error": str(e), "cas": 0, "status": False})
        return result

    def crud(self, op_type, key, value=None, exp=0, replicate_to=0,
             persist_to=0, durability="",
             timeout=5, time_unit="seconds",
             create_path=True, xattr=False, cas=0, sdk_retry_strategy=None,
             store_semantics=None, preserve_expiry=None, access_deleted=False,
             create_as_deleted=False, identifier_token=IDENTIFIER_TOKEN):
        result = None
        if op_type == DocLoading.Bucket.DocOps.UPDATE:
            result = self.upsert(
                key, value, exp=exp,
                persist_to=persist_to, replicate_to=replicate_to,
                durability=durability,
                timeout=timeout, time_unit=time_unit,
                sdk_retry_strategy=sdk_retry_strategy,
                preserve_expiry=preserve_expiry, identifier_token=identifier_token)
        elif op_type == DocLoading.Bucket.DocOps.CREATE:
            result = self.insert(
                key, value, exp=exp,
                persist_to=persist_to, replicate_to=replicate_to,
                durability=durability,
                timeout=timeout, time_unit=time_unit,
                sdk_retry_strategy=sdk_retry_strategy)
        elif op_type == DocLoading.Bucket.DocOps.DELETE:
            result = self.delete(
                key,
                persist_to=persist_to, replicate_to=replicate_to,
                durability=durability,
                timeout=timeout, time_unit=time_unit,
                sdk_retry_strategy=sdk_retry_strategy)
        elif op_type == DocLoading.Bucket.DocOps.REPLACE:
            result = self.replace(
                key, value, exp=exp,
                persist_to=persist_to, replicate_to=replicate_to,
                durability=durability,
                timeout=timeout, time_unit=time_unit,
                cas=cas,
                sdk_retry_strategy=sdk_retry_strategy,
                preserve_expiry=preserve_expiry)
        elif op_type == DocLoading.Bucket.DocOps.TOUCH:
            result = self.touch(
                key, exp=exp,
                persist_to=persist_to, replicate_to=replicate_to,
                durability=durability,
                timeout=timeout, time_unit=time_unit,
                sdk_retry_strategy=sdk_retry_strategy)
        elif op_type == DocLoading.Bucket.DocOps.READ:
            result = self.read(
                key, timeout=timeout, time_unit=time_unit,
                access_deleted=access_deleted,
                sdk_retry_strategy=sdk_retry_strategy)
        # elif op_type in [DocLoading.Bucket.SubDocOps.INSERT, "subdoc_insert"]:
        #     sub_key, value = value[0], value[1]
        #     path_val = dict()
        #     path_val[key] = [(sub_key, value)]
        #     mutate_in_specs = list()
        #     mutate_in_specs.append(RESTClient.sub_doc_op.getInsertMutateInSpec(
        #         sub_key, value, create_path, xattr))
        #     if not xattr:
        #         mutate_in_specs.append(
        #             RESTClient.sub_doc_op.getIncrMutateInSpec("mutated", 1,
        #                                                       create_path))
        #     content = Tuples.of(key, mutate_in_specs)
        #     options = SDKOptions.get_mutate_in_options(
        #         exp, time_unit, persist_to, replicate_to,
        #         timeout, time_unit, durability,
        #         store_semantics=store_semantics,
        #         preserve_expiry=preserve_expiry,
        #         sdk_retry_strategy=sdk_retry_strategy,
        #         access_deleted=access_deleted,
        #         create_as_deleted=create_as_deleted)
        #     if cas > 0:
        #         options = options.cas(cas)
        #
        #     result = RESTClient.sub_doc_op.bulkSubDocOperation(
        #         self.collection, [content], options)
        #     return self.__translate_upsert_multi_sub_doc_result(result, path_val)
        # elif op_type in [DocLoading.Bucket.SubDocOps.UPSERT, "subdoc_upsert"]:
        #     sub_key, value = value[0], value[1]
        #     path_val = dict()
        #     path_val[key] = [(sub_key, value)]
        #     mutate_in_specs = list()
        #     mutate_in_specs.append(RESTClient.sub_doc_op.getUpsertMutateInSpec(
        #         sub_key, value, create_path, xattr))
        #     if not xattr:
        #         mutate_in_specs.append(
        #             RESTClient.sub_doc_op.getIncrMutateInSpec("mutated", 1,
        #                                                       create_path))
        #     content = Tuples.of(key, mutate_in_specs)
        #     options = SDKOptions.get_mutate_in_options(
        #         exp, time_unit, persist_to, replicate_to,
        #         timeout, time_unit, durability,
        #         store_semantics=store_semantics,
        #         preserve_expiry=preserve_expiry,
        #         sdk_retry_strategy=sdk_retry_strategy,
        #         create_as_deleted=create_as_deleted)
        #     if cas > 0:
        #         options = options.cas(cas)
        #     result = RESTClient.sub_doc_op.bulkSubDocOperation(
        #         self.collection, [content], options)
        #     return self.__translate_upsert_multi_sub_doc_result(result, path_val)
        # elif op_type in [DocLoading.Bucket.SubDocOps.REMOVE, "subdoc_delete"]:
        #     mutate_in_specs = list()
        #     path_val = dict()
        #     path_val[key] = [(value, '')]
        #     mutate_in_specs.append(RESTClient.sub_doc_op.getRemoveMutateInSpec(
        #         value, xattr))
        #     if not xattr:
        #         mutate_in_specs.append(
        #             RESTClient.sub_doc_op.getIncrMutateInSpec("mutated", 1,
        #                                                       False))
        #     content = Tuples.of(key, mutate_in_specs)
        #     options = SDKOptions.get_mutate_in_options(
        #         exp, time_unit, persist_to, replicate_to,
        #         timeout, time_unit, durability,
        #         store_semantics=store_semantics,
        #         preserve_expiry=preserve_expiry,
        #         sdk_retry_strategy=sdk_retry_strategy,
        #         access_deleted=access_deleted,
        #         create_as_deleted=create_as_deleted)
        #     if cas > 0:
        #         options = options.cas(cas)
        #     result = RESTClient.sub_doc_op.bulkSubDocOperation(
        #         self.collection, [content], options)
        #     result = self.__translate_upsert_multi_sub_doc_result(result, path_val)
        # elif op_type == "subdoc_replace":
        #     sub_key, value = value[0], value[1]
        #     path_val = dict()
        #     path_val[key] = [(sub_key, value)]
        #     mutate_in_specs = list()
        #     mutate_in_specs.append(RESTClient.sub_doc_op.getReplaceMutateInSpec(
        #         sub_key, value, xattr))
        #     if not xattr:
        #         mutate_in_specs.append(
        #             RESTClient.sub_doc_op.getIncrMutateInSpec("mutated", 1,
        #                                                       False))
        #     content = Tuples.of(key, mutate_in_specs)
        #     options = SDKOptions.get_mutate_in_options(
        #         exp, time_unit, persist_to, replicate_to,
        #         timeout, time_unit, durability,
        #         store_semantics=store_semantics,
        #         preserve_expiry=preserve_expiry,
        #         sdk_retry_strategy=sdk_retry_strategy,
        #         create_as_deleted=create_as_deleted)
        #     if cas > 0:
        #         options = options.cas(cas)
        #     result = RESTClient.sub_doc_op.bulkSubDocOperation(
        #         self.collection, [content], options)
        #     result = self.__translate_upsert_multi_sub_doc_result(result, path_val)
        # elif op_type in [DocLoading.Bucket.SubDocOps.LOOKUP, "subdoc_read"]:
        #     mutate_in_specs = list()
        #     path_val = dict()
        #     path_val[key] = [(value, '')]
        #     mutate_in_specs.append(
        #         RESTClient.sub_doc_op.getLookUpInSpec(value, xattr))
        #     content = Tuples.of(key, mutate_in_specs)
        #     lookup_in_options = RESTClient.sub_doc_op.getLookupInOptions(access_deleted)
        #     result = RESTClient.sub_doc_op.bulkGetSubDocOperation(
        #         self.collection, [content], lookup_in_options)
        #     result = self.__translate_get_multi_sub_doc_result(result, path_val)
        # elif op_type == DocLoading.Bucket.SubDocOps.COUNTER:
        #     sub_key, step_value = value[0], value[1]
        #     mutate_in_specs = list()
        #     if not xattr:
        #         mutate_in_specs.append(
        #             RESTClient.sub_doc_op.getIncrMutateInSpec(sub_key,
        #                                                       step_value,
        #                                                       create_path))
        #     content = Tuples.of(key, mutate_in_specs)
        #     options = SDKOptions.get_mutate_in_options(
        #         exp, time_unit, persist_to, replicate_to,
        #         timeout, time_unit, durability,
        #         store_semantics=store_semantics,
        #         preserve_expiry=preserve_expiry,
        #         sdk_retry_strategy=sdk_retry_strategy)
        #     if cas > 0:
        #         options = options.cas(cas)
        #     result = RESTClient.sub_doc_op.bulkSubDocOperation(
        #         self.collection, [content], options)
        #     result = self.__translate_upsert_multi_sub_doc_result(result)
        else:
            self.log.error("Unsupported operation %s" % op_type)
        return result

    def close(self):
        pass

    # def get_from_all_replicas(self, key):
    #     result = []
    #     get_result = self.collection.getAllReplicas(
    #         key, GetAllReplicasOptions.getAllReplicasOptions())
    #     try:
    #         get_result = get_result.toArray()
    #         if get_result:
    #             for item in get_result:
    #                 result.append({"key": key,
    #                                "value": item.contentAsObject(),
    #                                "cas": item.cas(), "status": True})
    #     except Exception:
    #         pass
    #     return result
    #

    # # Bulk CRUD APIs
    # def delete_multi(self, keys, persist_to=0, replicate_to=0,
    #                  timeout=5, time_unit=SDKConstants.TimeUnit.SECONDS,
    #                  durability="", sdk_retry_strategy=None):
    #     options = SDKOptions.get_remove_options(
    #         persist_to=persist_to, replicate_to=replicate_to,
    #         timeout=timeout, time_unit=time_unit,
    #         durability=durability,
    #         sdk_retry_strategy=sdk_retry_strategy)
    #     result = RESTClient.doc_op.bulkDelete(
    #         self.collection, keys, options)
    #     return self.__translate_delete_multi_results(result)
    #
    # def touch_multi(self, keys, exp=0,
    #                 timeout=5, time_unit=SDKConstants.TimeUnit.SECONDS,
    #                 sdk_retry_strategy=None):
    #     touch_options = SDKOptions.get_touch_options(
    #         timeout, time_unit, sdk_retry_strategy=sdk_retry_strategy)
    #     exp_duration = \
    #         SDKOptions.get_duration(exp, SDKConstants.TimeUnit.SECONDS)
    #     result = RESTClient.doc_op.bulkTouch(
    #         self.collection, keys, exp,
    #         touch_options, exp_duration)
    #     return self.__translate_delete_multi_results(result)
    #
    # def set_multi(self, items, exp=0, exp_unit=SDKConstants.TimeUnit.SECONDS,
    #               persist_to=0, replicate_to=0,
    #               timeout=5, time_unit=SDKConstants.TimeUnit.SECONDS,
    #               doc_type="json", durability="", sdk_retry_strategy=None):
    #     options = SDKOptions.get_insert_options(
    #         exp=exp, exp_unit=exp_unit,
    #         persist_to=persist_to, replicate_to=replicate_to,
    #         timeout=timeout, time_unit=time_unit,
    #         durability=durability,
    #         sdk_retry_strategy=sdk_retry_strategy)
    #     if doc_type.lower() == "binary":
    #         options = options.transcoder(RawBinaryTranscoder.INSTANCE)
    #     elif doc_type.lower() == "string":
    #         options = options.transcoder(RawStringTranscoder.INSTANCE)
    #     result = RESTClient.doc_op.bulkInsert(
    #         self.collection, items, options)
    #     return self.__translate_upsert_multi_results(result)
    #
    # def upsert_multi(self, docs,
    #                  exp=0, exp_unit=SDKConstants.TimeUnit.SECONDS,
    #                  persist_to=0, replicate_to=0,
    #                  timeout=5, time_unit=SDKConstants.TimeUnit.SECONDS,
    #                  doc_type="json", durability="",
    #                  preserve_expiry=None, sdk_retry_strategy=None):
    #     options = SDKOptions.get_upsert_options(
    #         exp=exp, exp_unit=exp_unit,
    #         persist_to=persist_to, replicate_to=replicate_to,
    #         timeout=timeout, time_unit=time_unit,
    #         durability=durability,
    #         preserve_expiry=preserve_expiry,
    #         sdk_retry_strategy=sdk_retry_strategy)
    #     if doc_type.lower() == "binary":
    #         options = options.transcoder(RawBinaryTranscoder.INSTANCE)
    #     elif doc_type.lower() == "string":
    #         options = options.transcoder(RawStringTranscoder.INSTANCE)
    #     result = RESTClient.doc_op.bulkUpsert(
    #         self.collection, docs, options)
    #     return self.__translate_upsert_multi_results(result)
    #
    # def replace_multi(self, docs,
    #                   exp=0, exp_unit=SDKConstants.TimeUnit.SECONDS,
    #                   persist_to=0, replicate_to=0,
    #                   timeout=5, time_unit=SDKConstants.TimeUnit.SECONDS,
    #                   doc_type="json", durability="",
    #                   preserve_expiry=None, sdk_retry_strategy=None):
    #     options = SDKOptions.get_replace_options(
    #         exp=exp, exp_unit=exp_unit,
    #         persist_to=persist_to, replicate_to=replicate_to,
    #         timeout=timeout, time_unit=time_unit,
    #         durability=durability,
    #         preserve_expiry=preserve_expiry,
    #         sdk_retry_strategy=sdk_retry_strategy)
    #     if doc_type.lower() == "binary":
    #         options = options.transcoder(RawBinaryTranscoder.INSTANCE)
    #     elif doc_type.lower() == "string":
    #         options = options.transcoder(RawStringTranscoder.INSTANCE)
    #     result = RESTClient.doc_op.bulkReplace(
    #         self.collection, docs, options)
    #     return self.__translate_upsert_multi_results(result)
    #
    # def get_multi(self, keys,
    #               timeout=5, time_unit=SDKConstants.TimeUnit.SECONDS,
    #               sdk_retry_strategy=None):
    #     read_options = SDKOptions.get_read_options(
    #         timeout, time_unit,
    #         sdk_retry_strategy=sdk_retry_strategy)
    #     result = RESTClient.doc_op.bulkGet(self.collection, keys, read_options)
    #     return self.__translate_get_multi_results(result)
    #
    # # Bulk CRUDs for sub-doc APIs
    # def sub_doc_insert_multi(self, keys,
    #                          exp=0, exp_unit=SDKConstants.TimeUnit.SECONDS,
    #                          persist_to=0, replicate_to=0,
    #                          timeout=5,
    #                          time_unit=SDKConstants.TimeUnit.SECONDS,
    #                          durability="",
    #                          create_path=False,
    #                          xattr=False,
    #                          cas=0,
    #                          store_semantics=None,
    #                          preserve_expiry=None,
    #                          sdk_retry_strategy=None,
    #                          access_deleted=False,
    #                          create_as_deleted=False):
    #     """
    #
    #     :param keys: Documents to perform sub_doc operations on.
    #     Must be a dictionary with Keys and List of tuples for
    #     path and value.
    #     :param exp: Expiry of document
    #     :param exp_unit: Expiry time unit
    #     :param persist_to: Persist to parameter
    #     :param replicate_to: Replicate to parameter
    #     :param timeout: timeout for the operation
    #     :param time_unit: timeout time unit
    #     :param durability: Durability level parameter
    #     :param create_path: Boolean used to create sub_doc path if not exists
    #     :param xattr: Boolean. If 'True', perform xattr operation
    #     :param cas: CAS for the document to use
    #     :param store_semantics: Extra action to take during mutate_in op
    #     :param preserve_expiry: Boolean to preserver ttl of the doc or not
    #     :param sdk_retry_strategy: Sets sdk_retry_strategy for doc ops
    #     :param access_deleted: Allows editing documents in Tombstones form
    #     :param create_as_deleted: Allows creating documents in Tombstone form
    #     :return:
    #     """
    #     mutate_in_specs = []
    #     path_val = dict()
    #     for item in keys:
    #         key = item.getT1()
    #         value = item.getT2()
    #         mutate_in_spec = []
    #         path_val[key] = value
    #         for _tuple in value:
    #             _path = _tuple[0]
    #             _val = _tuple[1]
    #             _mutate_in_spec = RESTClient.sub_doc_op.getInsertMutateInSpec(
    #                 _path, _val, create_path, xattr)
    #             mutate_in_spec.append(_mutate_in_spec)
    #         if not xattr:
    #             _mutate_in_spec = RESTClient.sub_doc_op.getIncrMutateInSpec(
    #                 "mutated", 1, True)
    #             mutate_in_spec.append(_mutate_in_spec)
    #         content = Tuples.of(key, mutate_in_spec)
    #         mutate_in_specs.append(content)
    #     options = SDKOptions.get_mutate_in_options(
    #         exp, exp_unit, persist_to, replicate_to,
    #         timeout, time_unit,
    #         durability,
    #         store_semantics=store_semantics,
    #         preserve_expiry=preserve_expiry,
    #         sdk_retry_strategy=sdk_retry_strategy,
    #         access_deleted=access_deleted,
    #         create_as_deleted=create_as_deleted)
    #     if cas > 0:
    #         options = options.cas(cas)
    #     result = RESTClient.sub_doc_op.bulkSubDocOperation(
    #         self.collection, mutate_in_specs, options)
    #     return self.__translate_upsert_multi_sub_doc_result(result, path_val)
    #
    # def sub_doc_upsert_multi(self, keys, exp=0,
    #                          exp_unit=SDKConstants.TimeUnit.SECONDS,
    #                          persist_to=0, replicate_to=0,
    #                          timeout=5,
    #                          time_unit=SDKConstants.TimeUnit.SECONDS,
    #                          durability="",
    #                          create_path=False,
    #                          xattr=False,
    #                          cas=0,
    #                          store_semantics=None,
    #                          preserve_expiry=None,
    #                          sdk_retry_strategy=None,
    #                          access_deleted=False,
    #                          create_as_deleted=False):
    #     """
    #     :param keys: Documents to perform sub_doc operations on.
    #     Must be a dictionary with Keys and List of tuples for
    #     path and value.
    #     :param exp: Expiry of document
    #     :param exp_unit: Expiry time unit
    #     :param persist_to: Persist to parameter
    #     :param replicate_to: Replicate to parameter
    #     :param timeout: timeout for the operation
    #     :param time_unit: timeout time unit
    #     :param durability: Durability level parameter
    #     :param create_path: Boolean used to create sub_doc path if not exists
    #     :param xattr: Boolean. If 'True', perform xattr operation
    #     :param cas: CAS for the document to use
    #     :param store_semantics: Extra action to take during mutate_in op
    #     :param preserve_expiry: Boolean to preserver ttl of the doc or not
    #     :param sdk_retry_strategy: Sets sdk_retry_strategy for doc ops
    #     :return:
    #     """
    #     mutate_in_specs = []
    #     path_val = dict()
    #     for kv in keys:
    #         key = kv.getT1()
    #         value = kv.getT2()
    #         mutate_in_spec = []
    #         path_val[key] = value
    #         for _tuple in value:
    #             _path = _tuple[0]
    #             _val = _tuple[1]
    #             _mutate_in_spec = RESTClient.sub_doc_op.getUpsertMutateInSpec(
    #                 _path, _val, create_path, xattr)
    #             mutate_in_spec.append(_mutate_in_spec)
    #         if not xattr:
    #             _mutate_in_spec = RESTClient.sub_doc_op.getIncrMutateInSpec(
    #                 "mutated", 1, True)
    #             mutate_in_spec.append(_mutate_in_spec)
    #         content = Tuples.of(key, mutate_in_spec)
    #         mutate_in_specs.append(content)
    #     options = SDKOptions.get_mutate_in_options(
    #         exp, exp_unit, persist_to, replicate_to,
    #         timeout, time_unit,
    #         durability,
    #         store_semantics=store_semantics,
    #         preserve_expiry=preserve_expiry,
    #         sdk_retry_strategy=sdk_retry_strategy,
    #         access_deleted=access_deleted,
    #         create_as_deleted=create_as_deleted)
    #     if cas > 0:
    #         options = options.cas(cas)
    #     result = RESTClient.sub_doc_op.bulkSubDocOperation(
    #         self.collection, mutate_in_specs, options)
    #     return self.__translate_upsert_multi_sub_doc_result(result, path_val)
    #
    # def sub_doc_read_multi(self, keys, timeout=5,
    #                        time_unit=SDKConstants.TimeUnit.SECONDS,
    #                        xattr=False, access_deleted=False):
    #     """
    #     :param keys: List of tuples (key,value)
    #     :param timeout: timeout for the operation
    #     :param time_unit: timeout time unit
    #     :param xattr: Bool to enable xattr read
    #     :return:
    #     """
    #     mutate_in_specs = []
    #     path_val = dict()
    #     for kv in keys:
    #         key = kv.getT1()
    #         value = kv.getT2()
    #         path_val[key] = list()
    #         mutate_in_spec = []
    #         for _tuple in value:
    #             _path = _tuple[0]
    #             path_val[key].append((_path, ''))
    #             _mutate_in_spec = RESTClient.sub_doc_op.getLookUpInSpec(
    #                 _path,
    #                 xattr)
    #             mutate_in_spec.append(_mutate_in_spec)
    #         content = Tuples.of(key, mutate_in_spec)
    #         mutate_in_specs.append(content)
    #     lookup_in_options = RESTClient.sub_doc_op.getLookupInOptions(access_deleted)
    #     result = RESTClient.sub_doc_op.bulkGetSubDocOperation(
    #         # timeout, time_unit,
    #         self.collection, mutate_in_specs, lookup_in_options)
    #     return self.__translate_get_multi_sub_doc_result(result, path_val)
    #
    # def sub_doc_remove_multi(self, keys, exp=0,
    #                          exp_unit=SDKConstants.TimeUnit.SECONDS,
    #                          persist_to=0, replicate_to=0,
    #                          timeout=5,
    #                          time_unit=SDKConstants.TimeUnit.SECONDS,
    #                          durability="",
    #                          xattr=False,
    #                          cas=0,
    #                          store_semantics=None,
    #                          preserve_expiry=None,
    #                          sdk_retry_strategy=None,
    #                          access_deleted=False,
    #                          create_as_deleted=False):
    #     """
    #     :param keys: Documents to perform sub_doc operations on.
    #     Must be a dictionary with Keys and List of tuples for
    #     path and value.
    #     :param exp: Expiry of document
    #     :param exp_unit: Expiry time unit
    #     :param persist_to: Persist to parameter
    #     :param replicate_to: Replicate to parameter
    #     :param timeout: timeout for the operation
    #     :param time_unit: timeout time unit
    #     :param durability: Durability level parameter
    #     :param xattr: Boolean. If 'True', perform xattr operation
    #     :param cas: CAS for the document to use
    #     :param store_semantics: Value to be used in mutate_in_option
    #     :param preserve_expiry: Boolean to preserver ttl of the doc or not
    #     :param sdk_retry_strategy: Sets sdk_retry_strategy for doc ops
    #     :return:
    #     """
    #     mutate_in_specs = []
    #     path_val = dict()
    #     for kv in keys:
    #         mutate_in_spec = []
    #         key = kv.getT1()
    #         value = kv.getT2()
    #         path_val[key] = list()
    #         for _tuple in value:
    #             _path = _tuple[0]
    #             _val = _tuple[1]
    #             path_val[key].append((_path, ''))
    #             _mutate_in_spec = RESTClient.sub_doc_op.getRemoveMutateInSpec(
    #                 _path, xattr)
    #             mutate_in_spec.append(_mutate_in_spec)
    #         if not xattr:
    #             _mutate_in_spec = RESTClient.sub_doc_op.getIncrMutateInSpec(
    #                 "mutated", 1, False)
    #             mutate_in_spec.append(_mutate_in_spec)
    #         content = Tuples.of(key, mutate_in_spec)
    #         mutate_in_specs.append(content)
    #     options = SDKOptions.get_mutate_in_options(
    #         exp, exp_unit, persist_to, replicate_to,
    #         timeout, time_unit,
    #         durability,
    #         store_semantics=store_semantics,
    #         preserve_expiry=preserve_expiry,
    #         sdk_retry_strategy=sdk_retry_strategy,
    #         access_deleted=access_deleted,
    #         create_as_deleted=create_as_deleted)
    #     if cas > 0:
    #         options = options.cas(cas)
    #     result = RESTClient.sub_doc_op.bulkSubDocOperation(
    #         self.collection, mutate_in_specs, options)
    #     return self.__translate_upsert_multi_sub_doc_result(result, path_val)
    #
    # def sub_doc_replace_multi(self, keys, exp=0,
    #                           exp_unit=SDKConstants.TimeUnit.SECONDS,
    #                           persist_to=0, replicate_to=0,
    #                           timeout=5,
    #                           time_unit=SDKConstants.TimeUnit.SECONDS,
    #                           durability="",
    #                           xattr=False,
    #                           cas=0,
    #                           store_semantics=None,
    #                           preserve_expiry=None, sdk_retry_strategy=None,
    #                           access_deleted=False,
    #                           create_as_deleted=False):
    #     """
    #
    #     :param keys: Documents to perform sub_doc operations on.
    #     Must be a dictionary with Keys and List of tuples for
    #     path and value.
    #     :param exp: Expiry of document
    #     :param exp_unit: Expiry time unit
    #     :param persist_to: Persist to parameter
    #     :param replicate_to: Replicate to parameter
    #     :param timeout: timeout for the operation
    #     :param time_unit: timeout time unit
    #     :param durability: Durability level parameter
    #     :param xattr: Boolean. If 'True', perform xattr operation
    #     :param cas: CAS for the document to use
    #     :param store_semantics: Extra action to take during mutate_in op
    #     :param preserve_expiry: Boolean to preserver ttl of the doc or not
    #     :param sdk_retry_strategy: Sets sdk_retry_strategy for doc ops
    #     :return:
    #     """
    #     mutate_in_specs = []
    #     path_val = dict()
    #     for kv in keys:
    #         mutate_in_spec = []
    #         key = kv.getT1()
    #         value = kv.getT2()
    #         path_val[key] = value
    #         for _tuple in value:
    #             _path = _tuple[0]
    #             _val = _tuple[1]
    #             _mutate_in_spec = RESTClient.sub_doc_op.getReplaceMutateInSpec(
    #                 _path, _val, xattr)
    #             mutate_in_spec.append(_mutate_in_spec)
    #         if not xattr:
    #             _mutate_in_spec = RESTClient.sub_doc_op.getIncrMutateInSpec(
    #                 "mutated", 1, False)
    #             mutate_in_spec.append(_mutate_in_spec)
    #         content = Tuples.of(key, mutate_in_spec)
    #         mutate_in_specs.append(content)
    #     options = SDKOptions.get_mutate_in_options(
    #         exp, exp_unit, persist_to, replicate_to,
    #         timeout, time_unit,
    #         durability,
    #         store_semantics=store_semantics,
    #         preserve_expiry=preserve_expiry,
    #         sdk_retry_strategy=sdk_retry_strategy,
    #         access_deleted=access_deleted,
    #         create_as_deleted=create_as_deleted)
    #     if cas > 0:
    #         options = options.cas(cas)
    #     result = RESTClient.sub_doc_op.bulkSubDocOperation(
    #         self.collection, mutate_in_specs, options)
    #     return self.__translate_upsert_multi_sub_doc_result(result, path_val)
    #
    # def insert_binary_document(self, keys, sdk_retry_strategy=None):
    #     options = \
    #         SDKOptions.get_insert_options(
    #             sdk_retry_strategy=sdk_retry_strategy) \
    #             .transcoder(RawBinaryTranscoder.INSTANCE)
    #     for key in keys:
    #         binary_value = Unpooled.copiedBuffer('{value":"' + key + '"}',
    #                                              CharsetUtil.UTF_8)
    #         self.collection.upsert(key, binary_value, options)
    #
    # def insert_string_document(self, keys, sdk_retry_strategy=None):
    #     options = \
    #         SDKOptions.get_insert_options(
    #             sdk_retry_strategy=sdk_retry_strategy) \
    #             .transcoder(RawStringTranscoder.INSTANCE)
    #     for key in keys:
    #         self.collection.upsert(key, '{value":"' + key + '"}', options)
    #
    # def insert_custom_json_documents(self, key_prefix, documents):
    #     for index, data in enumerate(documents):
    #         self.collection.insert(key_prefix + str(index),
    #                                JsonObject.create().put("content", data))
    #
    # def insert_xattr_attribute(self, document_id, path, value, xattr=True,
    #                            create_parents=True):
    #     self.crud("subdoc_insert",
    #               document_id,
    #               [path, value],
    #               time_unit=SDKConstants.TimeUnit.SECONDS,
    #               create_path=create_parents,
    #               xattr=xattr)
    #
    # def update_xattr_attribute(self, document_id, path, value, xattr=True,
    #                            create_parents=True):
    #     self.crud("subdoc_upsert",
    #               document_id,
    #               [path, value],
    #               time_unit=SDKConstants.TimeUnit.SECONDS,
    #               create_path=create_parents,
    #               xattr=xattr)
    #
    # def insert_json_documents(self, key_prefix, documents):
    #     for index, data in enumerate(documents):
    #         self.collection.insert(key_prefix + str(index),
    #                                JsonObject.fromJson(data))
