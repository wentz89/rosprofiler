#!/usr/bin/env python
import rospy
import rosgraph
import rosnode
import xmlrpclib
from ros_topology_msgs.msg import *
import re



class Grapher(object):
    """ Publishes a latched ros_topology_msgs/Graph message describing the system state.
    Grapher uses the xmlrpc api to query information from the master and from individual nodes. 
    Only one instance of this node should be run at a time.
    Information is only published when a change is detected. 
    """
    def __init__(self, name=None):
        self._NAME = name or "Grapher"
        rospy.init_node(self._NAME)

        # Force singleton by not allowing name to be remapped
        if not re.findall("^/*(.+)$",rospy.get_name())[0] == self._NAME:
            raise rospy.ROSInitException("Node '%s' of type rosprofiler/grapher.py should only use the name '%s' to avoid being run multiple times."%(rospy.get_name(), self._NAME))

        self._master = rosgraph.Master(self._NAME)
        self._publisher = rospy.Publisher('/topology',Graph, queue_size = 10, latch = True)
        self._poller_timer = None

        # Message Sequence Number
        self._seq = 1

        # Previous values for comparison
        self._old_nodes = dict()
        self._old_topics = dict()

    def start(self):
        self._poller_timer = rospy.Timer(rospy.Duration(2), self._poller_callback)

    def stop(self):
        self._poller_timer.shutdown()
        self._poller_timer.join()

    def _poller_callback(self, event):
        """ Queries the state of the system using xmlrpc calls, and checks for changes """
        nodes = dict() # name: Node()
        topics = dict() # name: Topic()

        # Query master and compile a list of all published topics and their types
        allCurrentTopics = self._master.getTopicTypes()
        for topic,type_ in allCurrentTopics:
            topics[topic] = Topic(name=topic,type=type_)

        # Compile a list of nodes names and uris
        allCurrentNodes = rosnode.get_node_names()
        for name in allCurrentNodes:
            node = Node(name=name)
            try:
                node.uri = self._master.lookupNode(name)
            except rosgraph.masterapi.MasterError:
                rospy.logerr("WARNING: MasterAPI Error trying to contact '%s', skipping"%name)
                continue
            else:
                nodes[name] = node

        # Add lists of subscribers, publishers, and services for each topic
        systemstate = self._master.getSystemState()
        for topic_name, publisher_list in systemstate[0]:
            if not topic_name in topics.keys():
                rospy.logerr("Topic %s not found, skipping"%topic_name) 
            for publishername in publisher_list:
                nodes[publishername].publishes.append(topic_name)
        for topic_name, subscriber_list in systemstate[1]:
            if not topic_name in topics.keys():
                rospy.logerr("Topic %s not found, skipping"%topic_name) 
            for subscribername in subscriber_list:
                nodes[subscribername].subscribes.append(topic_name)
        for service_name, provider_list in systemstate[2]:
            for providername in provider_list:
                service = Service(name=service_name)
                try:
                    service.uri = self._master.lookupService(service_name)
                except rosgraph.masterapi.MasterError:
                    rospy.logerr("WARNING: MasterAPI Error trying to lookup service '%s', skipping"%service_name)
                    continue
                else:
                    nodes[providername].provides.append(service)

        # Add connection information reported by nodes
        for node in nodes.values():
            try:
                node_proxy = xmlrpclib.ServerProxy(node.uri)
                bus_info = node_proxy.getBusInfo(self._NAME)[2]
                for bus in bus_info:
                    c = Connection()
                    c.destination = bus[1]
                    c.direction = {'o': Connection.OUT, 'i': Connection.IN, 'b': Connection.BOTH}[bus[2]]
                    c.transport = bus[3]
                    c.topic = bus[4]
                    node.connections.append(c)
            except xmlrpclib.socket.error:
                rospy.logerr("WANRING: XML RPC ERROR contacting '%s', skipping"%node.name)
                continue

        # If any nodes or topics are added removed or changed, publish update
        if not set(nodes.keys()) == set(self._old_nodes.keys()):
            return self._update(nodes, topics)
        if not set(topics.keys()) == set(self._old_topics.keys()):
            return self._update(nodes, topics)
        for name in nodes.keys():
            if not nodes[name] == self._old_nodes[name]:
                return self._update(nodes, topics)

    def _update(self, nodes, topics):
        """ Publishes updated topology information """
        rospy.loginfo("Update detected, publishing revised topology")
        # Save old information
        self._old_nodes = nodes
        self._old_topics = topics

        graph = Graph()
        graph.header.seq = self._seq
        self._seq += 1
        graph.header.stamp = rospy.get_rostime()
        graph.header.frame_id = "/"
        graph.master = self._master.master_uri
        graph.nodes.extend(nodes.values())
        graph.topics.extend(topics.values())
        self._publisher.publish(graph)

if __name__ == '__main__':
    grapher = Grapher()
    grapher.start()
    rospy.spin()


