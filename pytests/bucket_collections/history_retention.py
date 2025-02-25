import json
import urllib
from copy import deepcopy
from random import choice, sample, randint

from BucketLib.BucketOperations import BucketHelper
from BucketLib.bucket import Bucket
from Cb_constants import DocLoading, CbServer
from basetestcase import ClusterSetup
from bucket_collections.collections_base import CollectionBase
from cb_tools.cb_cli import CbCli
from cb_tools.cbepctl import Cbepctl
from cb_tools.cbstats import Cbstats
from collections_helper.collections_spec_constants import MetaCrudParams
from couchbase_helper.documentgenerator import doc_generator
from error_simulation.cb_error import CouchbaseError
from remote.remote_util import RemoteMachineShellConnection


class DocHistoryRetention(ClusterSetup):
    def setUp(self):
        super(DocHistoryRetention, self).setUp()
        self.shells = dict()
        self.spec_name = self.input.param("bucket_spec", None)
        self.data_spec_name = self.input.param("data_spec_name",
                                               "initial_load")
        if self.spec_name:
            self.bucket_util.add_rbac_user(self.cluster.master)
            CollectionBase.deploy_buckets_from_spec_file(self)
            CollectionBase.create_clients_for_sdk_pool(self)
            CollectionBase.load_data_from_spec_file(self, self.data_spec_name)

        # Prints bucket stats
        self.bucket_util.print_bucket_stats(self.cluster)

        self.log.info("Opening shell connections")
        for node in self.cluster.nodes_in_cluster:
            self.shells[node] = RemoteMachineShellConnection(node)

    def tearDown(self):
        self.log.info("Closing shell connections")
        for _, shell in self.shells.items():
            shell.disconnect()

        super(DocHistoryRetention, self).tearDown()

    def __create_bucket(self, params):
        bucket_helper = BucketHelper(self.cluster.master)
        api = bucket_helper.baseUrl + "pools/default/buckets"
        self.log.info("Create bucket with params: %s" % params)
        params = urllib.urlencode(params)
        status, content, _ = bucket_helper._http_request(api, "POST", params)
        return status, content

    def __set_history_retention_for_scope(self, bucket, scope, history):
        for c_name, col in scope.collections.items():
            if c_name == CbServer.default_collection:
                continue
            self.bucket_util.set_history_retention_for_collection(
                self.cluster.master, bucket, scope.name, c_name, history)

    def __validate_dedupe_with_data_load(
            self, bucket,
            scope=CbServer.default_scope,
            collection=CbServer.default_collection):
        def populate_stats(b_obj, stat_dict):
            for node, t_shell in self.shells.items():
                if node not in stat_dict:
                    stat_dict[node] = dict()
                cbstats = Cbstats(node)
                all_stats = cbstats.all_stats(b_obj.name)
                dcp_stats = cbstats.dcp_stats(b_obj.name)
                all_fields = ["ep_total_enqueued", "ep_total_persisted",
                              "ep_total_deduplicated"]
                items_sent = "ep_dcp_items_sent"
                for field in all_fields:
                    stat_dict[node][field] = all_stats[field]
                stat_dict[node][items_sent] = dcp_stats[items_sent]

        if bucket.bucketType == Bucket.Type.EPHEMERAL:
            return

        stat_data = dict()
        stat_data["before_ops"] = dict()
        stat_data["after_ops"] = dict()

        populate_stats(bucket, stat_data["before_ops"])

        num_items = 1000
        iterations = 20
        doc_gen = doc_generator(self.key, 0, num_items)
        load_task = self.task.async_load_gen_docs(
            self.cluster, bucket, doc_gen, DocLoading.Bucket.DocOps.UPDATE,
            print_ops_rate=False, batch_size=500, process_concurrency=2,
            iterations=iterations, scope=scope, collection=collection,
            sdk_client_pool=self.sdk_client_pool)
        self.task_manager.get_task_result(load_task)
        self.bucket_util._wait_for_stats_all_buckets(self.cluster, [bucket])

        populate_stats(bucket, stat_data["after_ops"])
        history = False
        if bucket.scopes[scope].collections[collection].history == "true":
            history = True
        self.log.info("%s:%s:%s, History: %s"
                      % (bucket.name, scope, collection, history))
        num_mutations = num_items * iterations
        expected_dcp_items_to_send = num_mutations * bucket.replicaNumber
        total_dcp_items_sent = 0
        total_enqueued = 0
        total_persisted = 0
        for t_node, _ in self.shells.items():
            self.log.debug("%s: %s" % (t_node.ip, stat_data))
            key = "ep_total_deduplicated"
            dedupe_before = int(stat_data["before_ops"][t_node][key])
            dedupe_after = int(stat_data["after_ops"][t_node][key])
            key = "ep_total_enqueued"
            total_persisted += int(stat_data["after_ops"][t_node][key]) \
                - int(stat_data["before_ops"][t_node][key])
            key = "ep_total_persisted"
            total_enqueued += int(stat_data["after_ops"][t_node][key]) \
                - int(stat_data["before_ops"][t_node][key])
            key = "ep_dcp_items_sent"
            total_dcp_items_sent += int(stat_data["after_ops"][t_node][key]) \
                - int(stat_data["before_ops"][t_node][key])
            if history:
                self.assertEqual(dedupe_before, dedupe_after,
                                 "%s: Dedupe occurred" % t_node)
            else:
                self.assertNotEqual(dedupe_before, dedupe_after,
                                    "%s: No Dedupe" % t_node)

        if history:
            self.assertEqual(
                expected_dcp_items_to_send, total_dcp_items_sent,
                "Dcp sent stat mismatch, Actual: %s, expected: %s"
                % (total_dcp_items_sent, expected_dcp_items_to_send))
            self.assertTrue(
                total_enqueued == total_persisted \
                == expected_dcp_items_to_send + num_mutations,
                "Stat mismatch")

    @staticmethod
    def get_loader_spec(update_percent=0, update_itr=-1,
                        replace_percent=0, replace_itr=-1):
        return {
            "doc_crud": {
                MetaCrudParams.DocCrud.RANDOMIZE_VALUE: False,
                MetaCrudParams.DocCrud.COMMON_DOC_KEY: "test_collections",
                MetaCrudParams.DocCrud.CONT_UPDATE_PERCENT_PER_COLLECTION:
                    (update_percent, update_itr),
                MetaCrudParams.DocCrud.CONT_REPLACE_PERCENT_PER_COLLECTION:
                    (replace_percent, replace_itr),
            }
        }

    def run_data_ops_on_individual_collection(self, bucket):
        for s_name, scope in bucket.scopes.items():
            for c_name, _, in scope.collections.items():
                self.__validate_dedupe_with_data_load(bucket, s_name, c_name)

    def test_enable_history_on_default_collection(self):
        """
        1. Try to enable history on default collection and validate the impact
        """
        bucket = self.cluster.buckets[0]
        status, content = \
            self.bucket_util.set_history_retention_for_collection(
                self.cluster.master, bucket,
                CbServer.default_scope, CbServer.default_collection, True)
        self.assertFalse(status, "Enabled history on default collection")
        self.assertEqual(content, "",
                         "Mismatch in error mismatch")
        for node in self.cluster.nodes_in_cluster:
            self.bucket_util.validate_history_retention_settings(node, bucket)
        self.__validate_dedupe_with_data_load(bucket)

    def test_create_bucket_with_doc_history_enabled(self):
        """
        1. Create bucket with history retention enabled
        2. Perform doc ops to validate dedupe are disabled
        3. Disable history retention across the entire bucket
        4. Perform doc ops to validate the dedupes are enabled now
        5. Re-enable the history retention and validate with doc_ops
        """

        def validate_hist_retention_settings():
            for node in self.cluster.nodes_in_cluster:
                max_retry = 5
                while max_retry:
                    if self.bucket_util.validate_history_retention_settings(
                            node, bucket) is True:
                        break
                    max_retry -= 1
                    self.sleep(1, "Will retry to wait for history settings")
                else:
                    self.fail("Validation failed")

        create_by = self.input.param("create_by", "rest")
        expected_result = {
            Bucket.Type.EPHEMERAL: {
                Bucket.StorageBackend.couchstore: False
            },
            Bucket.Type.MEMBASE: {
                Bucket.StorageBackend.couchstore: False,
                Bucket.StorageBackend.magma: True
            }
        }
        bucket_params = {
            Bucket.name: self.bucket_util.get_random_name(),
            Bucket.ramQuotaMB: 256,
            Bucket.replicaNumber: self.num_replicas,
            Bucket.priority: Bucket.Priority.LOW,
            Bucket.flushEnabled: Bucket.FlushBucket.DISABLED,
            Bucket.evictionPolicy: self.bucket_eviction_policy,
            Bucket.conflictResolutionType: Bucket.ConflictResolution.SEQ_NO,
            Bucket.durabilityMinLevel: self.bucket_durability_level,
            Bucket.historyRetentionSeconds: self.bucket_dedup_retention_bytes,
            Bucket.historyRetentionBytes: self.bucket_dedup_retention_seconds}

        # Create bucket step
        for b_type, storage_data in expected_result.items():
            for storage_type, exp_outcome in storage_data.items():
                params = deepcopy(bucket_params)
                params[Bucket.bucketType] = b_type
                params[Bucket.storageBackend] = storage_type
                if b_type == Bucket.Type.EPHEMERAL:
                    params[Bucket.evictionPolicy] \
                        = Bucket.EvictionPolicy.NO_EVICTION
                bucket = Bucket(params)
                self.log.info(
                    "Create bucket '%s' with CDC enabled for %s:%s"
                    % (bucket.name, bucket.bucketType, bucket.storageBackend))
                if create_by == "rest":
                    status, output = self.__create_bucket(params)
                    bucket_created = status
                elif create_by == "cbcli":
                    bucket_created = False
                    params.pop(Bucket.conflictResolutionType)
                    if bucket.bucketType != Bucket.Type.MEMBASE:
                        params.pop(Bucket.storageBackend)
                        exp_err = \
                            "ERROR: --history-retention-bytes cannot be " \
                            "specified for a ephemeral bucket"
                    else:
                        exp_err = \
                            "ERROR: --history-retention-bytes cannot be " \
                            "specified for a bucket with couchstore backend"
                    cb_cli = CbCli(self.shells[self.cluster.master.ip])
                    output = cb_cli.create_bucket(params, wait=True)
                    if exp_outcome is True:
                        exp_err = "SUCCESS: Bucket created"
                        bucket_created = True
                    self.assertEqual(output[0].strip(), exp_err,
                                     "Unexpected cmd outcome: %s" % output)
                else:
                    self.fail("Invalid create_by '%s'" % create_by)

                self.assertEqual(bucket_created, exp_outcome,
                                 "Unexpected outcome for %s:%s"
                                 % (b_type, storage_type))
                if bucket_created is False:
                    # Cannot run docs_ops since bucket creation has failed
                    self.log.info("Validating error reason")
                    if create_by == "rest":
                        output = json.loads(output)
                        exp_err = "History Retention can only used with Magma"
                        self.assertEqual(
                            output["errors"]["historyRetentionSeconds"],
                            exp_err, "Mismatch in expected error")
                        self.assertEqual(
                            output["errors"]["historyRetentionBytes"],
                            exp_err, "Mismatch in expected error")
                    continue

                # Create scope/collections
                self.bucket_util.create_scope(
                    self.cluster.master, bucket, {"name": "scope_1"})
                self.bucket_util.create_collection(
                    self.cluster.master, bucket,
                    CbServer.default_scope, {"name": "c1", "history": "true"})
                self.bucket_util.create_collection(
                    self.cluster.master, bucket,
                    CbServer.default_scope, {"name": "c2", "history": "false"})
                self.bucket_util.create_collection(
                    self.cluster.master, bucket,
                    "scope_1", {"name": "c1", "history": "false"})
                self.bucket_util.create_collection(
                    self.cluster.master, bucket,
                    "scope_1", {"name": "c2", "history": "true"})

                validate_hist_retention_settings()
                self.log.info("Running doc_ops to validate the retention")
                self.run_data_ops_on_individual_collection(bucket)

                self.log.info("Disabling history retention")
                self.bucket_util.update_bucket_property(
                    self.cluster.master, bucket,
                    history_retention_seconds=0, history_retention_bytes=0)
                for s_name, scope in bucket.scopes.items():
                    for c_name, col in scope.collections.items():
                        if c_name == CbServer.default_collection:
                            continue
                        self.bucket_util.set_history_retention_for_collection(
                            self.cluster.master, bucket, s_name, c_name,
                            "false")

                validate_hist_retention_settings()
                self.log.info("Running doc_ops to validate the retention")
                self.run_data_ops_on_individual_collection(bucket)

                self.log.info("Re-enabling history retention")
                self.bucket_util.update_bucket_property(
                    self.cluster.master, bucket,
                    history_retention_seconds=self.bucket_dedup_retention_seconds,
                    history_retention_bytes=self.bucket_dedup_retention_bytes)
                for _, scope in bucket.scopes.items():
                    self.__set_history_retention_for_scope(bucket, scope,
                                                           "true")
                validate_hist_retention_settings()
                self.log.info("Running doc_ops to validate the retention")
                self.run_data_ops_on_individual_collection(bucket)

                self.log.info("Deleting the bucket")
                self.bucket_util.delete_all_buckets(self.cluster)

    def test_enabling_cdc_post_creation(self):
        """
        1. Try enabling CDC using desired method (rest / cb-cli / cb-epctl)
        2. Validate the results are as expected
        3. Disable and re-enable the retention history settings and validate
        """
        bucket = self.cluster.buckets[0]
        enable_by = self.input.param("enable_by", "rest")
        target_node = choice(self.cluster.nodes_in_cluster)
        exp_err = "History Retention can only used with Magma"

        is_history_valid = False
        if bucket.bucketType == Bucket.Type.MEMBASE \
                and bucket.storageBackend == Bucket.StorageBackend.magma:
            is_history_valid = True

        self.log.critical("Using node '%s' for testing" % target_node.ip)
        self.log.info("Trying to enable CDC using '%s'" % enable_by)
        if enable_by == "rest":
            try:
                result = self.bucket_util.update_bucket_property(
                    self.cluster.master, bucket,
                    history_retention_seconds=self.bucket_dedup_retention_seconds,
                    history_retention_bytes=self.bucket_dedup_retention_bytes)
                self.assertFalse(result, "Bucket update succeeded")
            except Exception as e:
                if not is_history_valid:
                    for t_key in ["historyRetentionSeconds",
                                  "historyRetentionBytes"]:
                        if '"%s":"%s"' % (t_key, exp_err) not in str(e):
                            self.fail("Enabled CDC for non-magma bucket")

            if is_history_valid:
                for _, scope in bucket.scopes.items():
                    self.__set_history_retention_for_scope(bucket, scope,
                                                           "true")
        elif enable_by == "cb_cli":
            shell = RemoteMachineShellConnection(target_node)
            cb_cli = CbCli(shell)
            result = cb_cli.edit_bucket(bucket.name)
            shell.disconnect()
            if is_history_valid:
                self.assertEqual(result, "Bucket updated successfully")
            else:
                self.assertEqual(result, "Bucket updation failed")
        elif enable_by == "cbepctl":
            shell = RemoteMachineShellConnection(target_node)
            cbepctl = Cbepctl(shell)
            time_result = cbepctl.set(bucket.name, "flush_param",
                                      "history_retention_seconds",
                                      self.bucket_dedup_retention_seconds)
            byte_result = cbepctl.set(bucket.name, "flush_param",
                                      "history_retention_bytes",
                                      self.bucket_dedup_retention_bytes)
            shell.disconnect()
            if is_history_valid:
                # Couchbase bucket + Magma storage case
                expected_time_output = [
                    'setting param: history_retention_seconds %s\n'
                    % self.bucket_dedup_retention_seconds,
                    "set history_retention_seconds to %s\n"
                    % self.bucket_dedup_retention_seconds]

                expected_bytes_output = [
                    "setting param: history_retention_bytes %s\n"
                    % self.bucket_dedup_retention_bytes,
                    "set history_retention_bytes to %s\n"
                    % self.bucket_dedup_retention_bytes]
                self.assertEqual(byte_result, expected_bytes_output,
                                 "Unexpected byte output: %s" % byte_result)
                self.assertEqual(time_result, expected_time_output,
                                 "Unexpected time output: %s" % byte_result)
                self.log.info("CDC enabling succeeded")
            else:
                err_line = \
                    'Error: EINVAL : Invalid packet : {"error":' \
                    '{"context":"Cannot sethistory_retention_%s : ' \
                    'requirements not met"}}\n'
                expected_bytes_err = [
                    'setting param: history_retention_bytes %s\n'
                    % self.bucket_dedup_retention_bytes,
                    err_line % "bytes"]
                expected_time_err = [
                    'setting param: history_retention_seconds %s\n'
                    % self.bucket_dedup_retention_seconds,
                    err_line % "seconds"]
                self.assertEqual(byte_result, expected_bytes_err,
                                 "Unexpected size_err msg: %s" % byte_result)
                self.assertEqual(time_result, expected_time_err,
                                 "Unexpected time_err msg: %s" % time_result)
                self.log.info("CDC enabling failed as expected")

        self.run_data_ops_on_individual_collection(bucket)
        if not is_history_valid:
            return

        self.log.info("Disabling history retention")
        self.bucket_util.update_bucket_property(
            self.cluster.master, bucket,
            history_retention_seconds=0, history_retention_bytes=0)
        for _, scope in bucket.scopes.items():
            self.__set_history_retention_for_scope(bucket, scope, "false")

        self.run_data_ops_on_individual_collection(bucket)

        self.log.info("Re-enabling history retention")
        self.bucket_util.update_bucket_property(
            self.cluster.master, bucket,
            history_retention_seconds=self.bucket_dedup_retention_seconds,
            history_retention_bytes=self.bucket_dedup_retention_bytes)
        for _, scope in bucket.scopes.items():
            self.__set_history_retention_for_scope(bucket, scope, "true")

        self.run_data_ops_on_individual_collection(bucket)

    def test_default_collection_retention_value(self):
        """
        1. Create bucket with 'historyRetentionCollectionDefault' value set
        2. Set retention_values as per the test config
        3. Create new collection and make sure it matches the default
           history_retention_param wrt the bucket
        """
        values_to_test = [
            {
                # Test the default value wrt bucket
                Bucket.historyRetentionCollectionDefault: None,
                Bucket.historyRetentionSeconds: None,
                Bucket.historyRetentionBytes: None,
            },
            {
                # Test the default value for collection-default
                Bucket.historyRetentionCollectionDefault: None,
                Bucket.historyRetentionSeconds: 0,
                Bucket.historyRetentionBytes: 0,
            },
            {
                Bucket.historyRetentionCollectionDefault: "false",
                Bucket.historyRetentionSeconds: 0,
                Bucket.historyRetentionBytes: 0,
            },
            {
                Bucket.historyRetentionCollectionDefault: "false",
                Bucket.historyRetentionSeconds: 1000,
                Bucket.historyRetentionBytes: 10240,
            },
            {
                Bucket.historyRetentionCollectionDefault: "true",
                Bucket.historyRetentionSeconds: 0,
                Bucket.historyRetentionBytes: 0,
            },
            {
                Bucket.historyRetentionCollectionDefault: "true",
                Bucket.historyRetentionSeconds: 1000,
                Bucket.historyRetentionBytes: 10240,
            },
        ]
        common_params = {
            Bucket.name: "default",
            Bucket.ramQuotaMB: 256,
            Bucket.storageBackend: self.bucket_storage,
            Bucket.replicaNumber: self.num_replicas,
            Bucket.priority: Bucket.Priority.LOW,
            Bucket.flushEnabled: Bucket.FlushBucket.DISABLED,
            Bucket.evictionPolicy: self.bucket_eviction_policy,
            Bucket.conflictResolutionType: Bucket.ConflictResolution.SEQ_NO,
            Bucket.durabilityMinLevel: self.bucket_durability_level
        }
        for to_test in values_to_test:
            bucket_params = deepcopy(common_params)
            for param in [Bucket.historyRetentionCollectionDefault,
                          Bucket.historyRetentionBytes,
                          Bucket.historyRetentionSeconds]:
                if to_test[param] is not None:
                    bucket_params[param] = to_test[param]

            bucket = Bucket(bucket_params)
            status, _ = self.__create_bucket(bucket_params)
            self.assertTrue(status, "Bucket creation failed")
            self.bucket_util.is_warmup_complete(self.cluster, [bucket])
            self.bucket_util.create_scope(self.cluster.master, bucket,
                                          scope_spec={"name": "scope_1"})
            self.bucket_util.create_collection(
                self.cluster.master, bucket,
                scope_name=CbServer.default_scope,
                collection_spec={"name": "c1", "history": "true"})
            self.bucket_util.create_collection(
                self.cluster.master, bucket,
                scope_name="scope_1",
                collection_spec={"name": "c2"})
            self.bucket_util.create_collection(
                self.cluster.master, bucket,
                scope_name="scope_1",
                collection_spec={"name": "c3", "history": "false"})
            result = True
            for node in self.cluster.nodes_in_cluster:
                result = result and \
                         self.bucket_util.validate_history_retention_settings(
                             node, bucket)
            self.bucket_util.delete_bucket(self.cluster, bucket,
                                           wait_for_bucket_deletion=True)
            self.assertTrue(result, "Validation failed")

    def test_cdc_for_selected_collections(self):
        """
        1. Create bucket with multiple collections with retention_history
        2. Selectively disable history for few scope / collections
        3. Perform dedupe mutations on individual collections to validate
        4. Drop few collections where history was disabled
        5. Recreate the same collection and make sure dedupe is enabled now
           (Taking the history from the scope)
        6. Disable history for a specific scope and validate using doc_ops
        7. Drop and recreate the same scope name and make sure history
           is enabled (taking the bucket's settings)
        """
        # Selecting collections to disable retention history
        bucket = self.cluster.buckets[0]
        scope_list = bucket.scopes.keys()
        collection_list = list()
        for s_name in scope_list:
            active_cols = self.bucket_util.get_active_collections(
                bucket, s_name, only_names=True)
            collection_list += [[s_name, c_name] for c_name in active_cols]
        selected_cols = sample(collection_list, 3)
        # Disable collection for history
        for scope_col in selected_cols:
            s_name, c_name = scope_col
            self.log.info("Disable history for %s::%s" % (s_name, c_name))
            self.bucket_util.update_history_for_collection(
                bucket, s_name, c_name, history=False)
            bucket.scopes[s_name].collections[c_name].history = False

        self.run_data_ops_on_individual_collection(bucket)
        for scope_col in selected_cols:
            s_name, c_name = scope_col
            self.log.info("Drop collection %s::%s" % (s_name, c_name))
            self.bucket_util.drop_collection(self.cluster.master, bucket,
                                             s_name, c_name)

    def test_update_retention_size_time_with_collections_disabled(self):
        """
        1. Create bucket with multiple scope / collections
        2. Set history=False to selective scope and collections
        3. Set new values to retention size / time
        4. Make sure the disabled scope / collection remains the same way
        """
        self.fail("WIP")

    def test_multi_bucket_retention_policy(self):
        """
        Create multiple buckets with variable retention policies
        1. With no retention policy configured (fall back to default)
        2. Time retention + default storage policy
        3. Storage retention + default time retention
        4. Bucket with multiple collections
        """
        def consecutive_data_load(data_load_spec):
            CollectionBase.over_ride_doc_loading_template_params(
                self, data_load_spec)
            CollectionBase.set_retry_exceptions_for_initial_data_load(
                self, data_load_spec)

            doc_loading_task = self.bucket_util.run_scenario_from_spec(
                self.task, self.cluster, self.cluster.buckets, data_load_spec,
                mutation_num=1, batch_size=500, process_concurrency=1)
            if doc_loading_task.result is False:
                self.fail("Doc_loading failed")

            self.bucket_util.print_bucket_stats(self.cluster)

        def validate_retention_settings():
            for node in self.cluster.nodes_in_cluster:
                result = self.bucket_util.validate_history_retention_settings(
                    node, self.cluster.buckets)
                self.assertTrue(result, "History retention validation failed")

        buckets_spec = self.bucket_util.get_bucket_template_from_package(
                "multi_bucket.history_retention_tests")
        # Process params to over_ride values if required
        CollectionBase.over_ride_bucket_template_params(self, buckets_spec)
        self.bucket_util.create_buckets_using_json_data(self.cluster,
                                                        buckets_spec)
        self.bucket_util.wait_for_collection_creation_to_complete(self.cluster)
        self.bucket_util.print_bucket_stats(self.cluster)
        validate_retention_settings()

        CollectionBase.create_clients_for_sdk_pool(self)
        CollectionBase.load_data_from_spec_file(self, "initial_load")
        # Consecutive data load
        data_spec = self.get_loader_spec(update_percent=1, update_itr=200)
        consecutive_data_load(data_spec)

        # Disable CDC for all the buckets
        for bucket in self.cluster.buckets:
            status = self.bucket_util.update_bucket_property(
                self.cluster.master, bucket,
                history_retention_bytes=0, history_retention_seconds=0)
            self.assertTrue(status, "Updating history settings failed")

        validate_retention_settings()
        # Consecutive data load
        data_spec = self.get_loader_spec(update_percent=1, update_itr=200)
        consecutive_data_load(data_spec)

        # Re-enable CDC for all the buckets
        for bucket in self.cluster.buckets:
            status = self.bucket_util.update_bucket_property(
                self.cluster.master, bucket,
                history_retention_bytes=self.bucket_dedup_retention_bytes,
                history_retention_seconds=self.bucket_dedup_retention_seconds)
            self.assertTrue(status, "Updating history settings failed")

        validate_retention_settings()
        data_spec = self.get_loader_spec(update_percent=1, update_itr=200)
        consecutive_data_load(data_spec)
        self.fail("Validate stats")

    def test_crash_active_node(self):
        cb_stat = dict()
        bucket = self.cluster.buckets[0]
        target_node = self.cluster.nodes_in_cluster[1]
        total_iterations = self.input.param("iterations", 10)

        self.log.info("Target node: %s, vbucket: %s"
                      % (target_node.ip, Bucket.vBucket.ACTIVE))
        for node in self.cluster.nodes_in_cluster:
            cb_stat[node.ip] = Cbstats(node)
        cb_err = CouchbaseError(self.log, self.shells[target_node])
        active_vbs = cb_stat[target_node.ip].vbucket_list(
            bucket, Bucket.vBucket.ACTIVE)
        self.log.info("Creating doc_generator")
        doc_gen = doc_generator(self.key, 0, self.num_items,
                                target_vbucket=active_vbs)

        while total_iterations > 0:
            cb_err.create(CouchbaseError.STOP_PERSISTENCE, bucket.name)
            self.log.info("Starting doc_loading")
            load_task = self.task.async_load_gen_docs(
                self.cluster, bucket, doc_gen, DocLoading.Bucket.DocOps.UPDATE,
                durability=self.durability_level, iterations=1000)
            self.task_manager.get_task_result(load_task)
            cb_err.create(CouchbaseError.KILL_MEMCACHED)
            total_iterations -= 1
            if total_iterations:
                self.sleep(10, "Wait before next iteration")

        self.fail("Validate roll back")

    def test_crash_replica_node(self):
        def stop_persistence_using_cbepctl():
            if target_scenario == CouchbaseError.STOP_PERSISTENCE:
                cb_err.create(CouchbaseError.STOP_PERSISTENCE, bucket.name)

        cb_stat = dict()
        bucket = self.cluster.buckets[0]
        target_node = self.cluster.nodes_in_cluster[1]
        target_scenario = self.input.param("scenario",
                                           CouchbaseError.STOP_PERSISTENCE)

        self.log.info("Target node: %s, vbucket: %s"
                      % (target_node.ip, Bucket.vBucket.REPLICA))
        for node in self.cluster.nodes_in_cluster:
            cb_stat[node.ip] = Cbstats(node)
        cb_err = CouchbaseError(self.log, self.shells[target_node])
        replica_vbs = cb_stat[target_node.ip].vbucket_list(
            bucket, Bucket.vBucket.REPLICA)
        self.log.info("Creating doc_generator")
        doc_gen = doc_generator(self.key, 0, self.num_items,
                                target_vbucket=replica_vbs)

        if target_scenario == CouchbaseError.STOP_MEMCACHED:
            cb_err.create(target_scenario)
        else:
            stop_persistence_using_cbepctl()

        self.log.info("Starting doc_loading")
        load_task = self.task.async_load_gen_docs(
            self.cluster, bucket, doc_gen, DocLoading.Bucket.DocOps.UPDATE,
            durability=self.durability_level, iterations=1000)
        if target_scenario != CouchbaseError.STOP_MEMCACHED:
            while not load_task.completed:
                self.log.info("Killing memcached")
                cb_err.create(CouchbaseError.KILL_MEMCACHED)
                self.sleep(5, "Wait for memcached to come back")
                stop_persistence_using_cbepctl()
        self.task_manager.get_task_result(load_task)

        self.log.info("Performing stat validation")
        active_stats = dict()
        replica_stats = dict()
        for node in self.cluster.nodes_in_cluster:
            all_stats = cb_stat[node.ip].all_stats(bucket.name)
            dcp_stats = cb_stat[node.ip].dcp_stats(bucket.name)
            if node.ip == target_node.ip:
                active_stats[node.ip] = dict()
                stat_dict = active_stats
            else:
                replica_stats[node.ip] = dict()
                stat_dict = replica_stats
            stat_dict["op_create"] = all_stats["ops_create"]
        self.fail("WIP")

    def test_stop_or_kill_memcached_in_random(self):
        """
        1. Start dedupe ops on all buckets
        2. Perform stop/kill memcached for 'iterations' times
        3. Stop load and validate the cluster is intact
        """
        iterations = self.input.param("iterations", 20)
        scenario = self.input.param("scenario", CouchbaseError.KILL_MEMCACHED)
        loader_spec = self.get_loader_spec(update_percent=10, update_itr=-1,
                                           replace_percent=10, replace_itr=-1)
        self.log.info("Starting doc_loading")
        doc_loading_task = \
            self.bucket_util.run_scenario_from_spec(
                self.task, self.cluster, self.cluster.buckets, loader_spec,
                mutation_num=1, batch_size=500, process_concurrency=1,
                async_load=True)

        self.log.info("Testing with scenario=%s" % scenario)
        for index in range(1, iterations+1):
            self.log.info("Iteration :: %s" % index)
            node = choice(self.cluster.kv_nodes)
            shell = RemoteMachineShellConnection(node)
            err = CouchbaseError(self.log, shell)
            err.create(scenario)
            if scenario == CouchbaseError.STOP_MEMCACHED:
                self.sleep(10, "Wait before resuming persistence")
                err.revert(scenario)
            else:
                self.sleep(5, "Wait for memcached to come up")
            shell.disconnect()

        self.log.info("Stopping cont. doc_loading tasks")
        doc_loading_task.stop_indefinite_doc_loading_tasks()
        self.task_manager.get_task_result(doc_loading_task)

    def test_replica_node_restart_with_delay(self):
        """
        1. Start dedupe ops on all buckets
        2. Bring down the replica node for sometime and restart it
        3. Stop the cont. load and validate the cluster is intact
        """
        bucket = self.cluster.buckets[0]
        iterations = self.input.param("iterations", 20)
        t_node = choice(self.cluster.kv_nodes)
        if self.cluster.master == t_node:
            self.cluster.master = self.cluster.nodes_in_cluster[1]
        self.log.info("Target node: %s" % t_node.ip)
        shell = RemoteMachineShellConnection(t_node)
        cb_stat = Cbstats(t_node)
        cb_err = CouchbaseError(self.log, shell)
        replica_vbs = cb_stat.vbucket_list(bucket.name, Bucket.vBucket.REPLICA)
        doc_gen = doc_generator(self.key, 0, self.num_items,
                                target_vbucket=replica_vbs)
        self.log.info("Starting dedupe doc_ops")
        load_task = self.task.async_load_gen_docs(
            self.cluster, bucket, doc_gen, DocLoading.Bucket.DocOps.UPDATE,
            durability=self.durability_level, iterations=-1,
            batch_size=100, process_concurrency=6)
        for index in range(1, iterations+1):
            self.log.info("Iteration :: %s" % iterations)
            cb_err.create(CouchbaseError.STOP_SERVER)
            self.bucket_util.update_bucket_property(self.cluster.master)
            self.log.info(30, "Wait before starting the node")
            cb_err.revert(CouchbaseError.STOP_SERVER)
            if not self.cluster_util.wait_for_ns_servers_or_assert(t_node):
                self.fail("Node not yet up")
            self.sleep(randint(1, 10), "Wait before next itr")

        load_task.end_task()
        self.fail("Validate stats")

    def test_rebalance_with_dedupe(self):
        """
        - Create bucket and load initial data
        - Start data loading on required collection(s)
        - Start requested rebalance operation with ops in parallel
        - Validate rebalance succeeds + no unwanted loading errors
        """
        target_vbs = list()
        bucket = self.cluster.buckets[0]
        load_on_particular_node = \
            self.input.param("target_load_on_single_node", False)
        validate_high_retention_warn = self.input.param(
            "validate_high_retention_warn", False)
        nodes_in = self.servers[self.nodes_init:self.nodes_init+self.nodes_in]
        nodes_out = self.cluster.nodes_in_cluster[
                    -(self.nodes_init-self.nodes_out):]
        s_name = choice(bucket.scopes.keys())
        c_name = choice(bucket.scopes[s_name].collections.keys())

        self.log.info("Starting doc_loading")
        if load_on_particular_node:
            nodes = list()
            if nodes_in:
                nodes.append(choice(nodes_in))
            if nodes_out:
                nodes.append(choice(nodes_out))
            for t_node in nodes:
                cb_stats = Cbstats(t_node)
                for vb_type in [Bucket.vBucket.ACTIVE, Bucket.vBucket.REPLICA]:
                    target_vbs.extend(
                        cb_stats.vbucket_list(bucket.name, vb_type))
            target_vbs = list(set(target_vbs))

            load_gen = doc_generator(self.key, 0, 10000,
                                     target_vbucket=target_vbs)
            doc_loading_task = self.task.async_load_gen_docs(
                self.cluster, bucket, load_gen,
                DocLoading.Bucket.DocOps.UPDATE, exp=self.maxttl,
                durability=self.durability_level, iterations=-1,
                batch_size=500, process_concurrency=8,
                scope=s_name, collection=c_name)
        elif validate_high_retention_warn:
            self.fail("Validate warning")
        else:
            loader_spec = self.get_loader_spec(1, 100000)
            doc_loading_task = \
                self.bucket_util.run_scenario_from_spec(
                    self.task, self.cluster, self.cluster.buckets, loader_spec,
                    mutation_num=1, batch_size=500, process_concurrency=1,
                    async_load=True)

        self.log.info("Performing rebalance")
        reb_task = self.task.rebalance(
            self.cluster.nodes_in_cluster,
            to_add=nodes_in, to_remove=nodes_out)
        self.task_manager.get_task_result(reb_task)

        self.log.info("Waiting for doc_loading to complete")
        if load_on_particular_node:
            doc_loading_task.end_task()
        else:
            doc_loading_task.stop_indefinite_doc_loading_tasks()
        self.task_manager.get_task_result(doc_loading_task)

        self.assertTrue(reb_task.result, "Rebalance failed")
        self.assertTrue(doc_loading_task.result, "Loading failed")
