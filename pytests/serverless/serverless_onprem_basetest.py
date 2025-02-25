from Cb_constants import CbServer
from basetestcase import ClusterSetup
from membase.api.rest_client import RestConnection


class ServerlessOnPremBaseTest(ClusterSetup):
    def setUp(self):
        super(ServerlessOnPremBaseTest, self).setUp()

        self.log_setup_status(self.__class__.__name__, "started")

        # Start of serverless specific params
        # bucket_limit -> cluster's tenantLimit
        self.bucket_limit = self.input.param("bucket_limit", None)
        # max_weight -> cluster's bucketWeightLimit
        self.max_weight = self.input.param("max_weight", None)
        self.desired_width = self.input.param("desired_width",  None)
        self.desired_weight = self.input.param("desired_weight", None)
        self.desired_ram = self.input.param("desired_ram", None)
        # End of serverless specific params

        # Split server_groups to a list
        self.server_groups = self.server_groups.split(':')

        self.kv_distribution_dict = dict()
        for az in self.server_groups:
            self.kv_distribution_dict[az] = 1

        if not self.skip_setup_cleanup:
            self.distribute_servers_across_available_zones()
            self.remove_empty_server_groups(self.cluster)
            self.assertTrue(
                RestConnection(self.cluster.master).is_cluster_balanced(),
                "Cluster is unbalanced")

        self.cluster_util.print_cluster_stats(self.cluster)
        self.log_setup_status(self.__class__.__name__, "completed")

    def tearDown(self):
        super(ServerlessOnPremBaseTest, self).tearDown()

    def add_sub_cluster(self, service=CbServer.Services.KV):
        """
        :param service: Service to perform scaling
        :return task_obj:

        Initiates the rebalance task for the desired service
        and returns the rebalance task object
        """
        rest = RestConnection(self.cluster.master)
        if service == CbServer.Services.KV:
            num_nodes = CbServer.Serverless.KV_SubCluster_Size
            server_group_info = dict()
            for zone in rest.get_zone_names():
                server_group_info[zone] = 1

            to_add = self.spare_nodes[:num_nodes]
            self.spare_nodes = self.spare_nodes[num_nodes:]

            return self.task.async_rebalance(
                self.cluster, to_add=to_add, to_remove=[],
                add_nodes_server_groups=server_group_info)
        self.log.critical("Service %s not yet supported" % service)

    def distribute_servers_across_available_zones(self):
        """
        1. Create required zones
        2. Equally distribute the servers across all groups
        3. Removed zones present apart from the user-defined zones
        """
        if self.server_groups == CbServer.default_server_group:
            # Retain current settings as it is
            return

        rest = RestConnection(self.cluster.master)
        server_groups_in_cluster = rest.get_zone_names()
        total_zones = len(self.server_groups)

        for zone in self.server_groups:
            if zone not in server_groups_in_cluster:
                # Add zone to the cluster
                rest.add_zone(zone)

        # Perform node distribution
        index = -1
        for node in rest.get_nodes():
            index += 1
            rest.shuffle_nodes_in_zones(
                [node], node.server_group,
                self.server_groups[index % total_zones])
                # ["%s:%s" % (node.ip, node.port)], node.server_group,
                # self.server_groups[index % total_zones])

    def remove_empty_server_groups(self, cluster):
        rest = RestConnection(cluster.master)
        for server_group_info in rest.get_all_zones_info()["groups"]:
            if len(server_group_info["nodes"]) == 0:
                self.log.debug("Removing server_group: %s"
                               % server_group_info["name"])
                rest.delete_zone(server_group_info["name"])
