import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer
import builtin_interfaces.msg
from builtin_interfaces.msg import Time, Duration

from std_srvs.srv import Trigger, SetBool
from std_msgs.msg import Bool, Header
from tf2_msgs.msg import TFMessage
from geometry_msgs.msg import TransformStamped
from sensor_msgs.msg import Image, CameraInfo
from sensor_msgs.msg import JointState
from geometry_msgs.msg import TwistWithCovarianceStamped, Twist, Pose
from nav_msgs.msg import Odometry


from bosdyn.api.spot import robot_command_pb2 as spot_command_pb2
from bosdyn.api import geometry_pb2, trajectory_pb2
from bosdyn.api.geometry_pb2 import Quaternion, SE2VelocityLimit
from bosdyn.client import math_helpers
import functools
import bosdyn.geometry
import tf2_ros


from spot_msgs.msg import Metrics
from spot_msgs.msg import LeaseArray, LeaseResource
from spot_msgs.msg import FootState, FootStateArray
from spot_msgs.msg import EStopState, EStopStateArray
from spot_msgs.msg import WiFiState
from spot_msgs.msg import PowerState
from spot_msgs.msg import BehaviorFault, BehaviorFaultState
from spot_msgs.msg import SystemFault, SystemFaultState
from spot_msgs.msg import BatteryState, BatteryStateArray
from spot_msgs.msg import Feedback
from spot_msgs.msg import MobilityParams
from spot_msgs.action import NavigateTo
from spot_msgs.action import Trajectory
from spot_msgs.srv import ListGraph
from spot_msgs.srv import SetLocomotion
from spot_msgs.srv import ClearBehaviorFault
from spot_msgs.srv import SetVelocity

#####DEBUG/RELEASE: RELATIVE PATH NOT WORKING IN DEBUG
# Release
from .ros_helpers import *
from .spot_wrapper import SpotWrapper
### Debug
# from ros_helpers import *
# from spot_wrapper import SpotWrapper

import logging
import threading

import signal
import sys

class SpotROS():
    """Parent class for using the wrapper.  Defines all callbacks and keeps the wrapper alive"""

    def __init__(self):
        self.spot_wrapper = None
        self.node = None

        self.callbacks = {}
        """Dictionary listing what callback to use for what data task"""
        self.callbacks["robot_state"] = self.RobotStateCB
        self.callbacks["metrics"] = self.MetricsCB
        self.callbacks["lease"] = self.LeaseCB
        self.callbacks["front_image"] = self.FrontImageCB
        self.callbacks["side_image"] = self.SideImageCB
        self.callbacks["rear_image"] = self.RearImageCB

    def RobotStateCB(self, results):
        """Callback for when the Spot Wrapper gets new robot state data.
        Args:
            results: FutureWrapper object of AsyncPeriodicQuery callback
        """
        state = self.spot_wrapper.robot_state

        if state:
            ## joint states ##
            joint_state = GetJointStatesFromState(state, self.spot_wrapper)
            self.joint_state_pub.publish(joint_state)

            ## TF ##
            tf_msg = GetTFFromState(state, self.spot_wrapper, self.mode_parent_odom_tf)
            if len(tf_msg.transforms) > 0:
                self.tf_pub.publish(tf_msg)

            # Odom Twist #
            twist_odom_msg = GetOdomTwistFromState(state, self.spot_wrapper)
            self.odom_twist_pub.publish(twist_odom_msg)

            # Odom #
            if self.mode_parent_odom_tf == 'vision':
                odom_msg = GetOdomFromState(state, self.spot_wrapper, use_vision=True)
            else:
                odom_msg = GetOdomFromState(state, self.spot_wrapper, use_vision=False)
            self.odom_pub.publish(odom_msg)

            # Feet #
            foot_array_msg = GetFeetFromState(state, self.spot_wrapper)
            self.feet_pub.publish(foot_array_msg)

            # EStop #
            estop_array_msg = GetEStopStateFromState(state, self.spot_wrapper)
            self.estop_pub.publish(estop_array_msg)

            # WIFI #
            wifi_msg = GetWifiFromState(state, self.spot_wrapper)
            self.wifi_pub.publish(wifi_msg)

            # Battery States #
            battery_states_array_msg = GetBatteryStatesFromState(state, self.spot_wrapper)
            self.battery_pub.publish(battery_states_array_msg)

            # Power State #
            power_state_msg = GetPowerStatesFromState(state, self.spot_wrapper)
            self.power_pub.publish(power_state_msg)

            # System Faults #
            system_fault_state_msg = GetSystemFaultsFromState(state, self.spot_wrapper)
            self.system_faults_pub.publish(system_fault_state_msg)

            # Behavior Faults #
            behavior_fault_state_msg = getBehaviorFaultsFromState(state, self.spot_wrapper)
            self.behavior_faults_pub.publish(behavior_fault_state_msg)

    def MetricsCB(self, results):
        """Callback for when the Spot Wrapper gets new metrics data.
        Args:
            results: FutureWrapper object of AsyncPeriodicQuery callback
        """
        metrics = self.spot_wrapper.metrics
        if metrics:
            metrics_msg = Metrics()
            local_time = self.spot_wrapper.robotToLocalTime(metrics.timestamp)
            metrics_msg.header.stamp = Time(sec= local_time.seconds, nanosec = local_time.nanos)

            for metric in metrics.metrics:
                if metric.label == "distance":
                    metrics_msg.distance = metric.float_value
                if metric.label == "gait cycles":
                    metrics_msg.gait_cycles = metric.int_value
                if metric.label == "time moving":
                    #metrics_msg.time_moving = Time(metric.duration.seconds, metric.duration.nanos)
                    duration = Time(sec = metric.duration.seconds, nanosec = metric.duration.nanos)
                    metrics_msg.time_moving = duration
                if metric.label == "electric power":
                    #metrics_msg.electric_power = Time(metric.duration.seconds, metric.duration.nanos)
                    duration = Time(metric.duration.seconds, metric.duration.nanos)
                    metrics_msg.electric_power = duration.to_msg()
            self.metrics_pub.publish(metrics_msg)

    def LeaseCB(self, results):
        """Callback for when the Spot Wrapper gets new lease data.
        Args:
            results: FutureWrapper object of AsyncPeriodicQuery callback
        """
        lease_array_msg = LeaseArray()
        lease_list = self.spot_wrapper.lease
        if lease_list:
            for resource in lease_list:
                new_resource = LeaseResource()
                new_resource.resource = resource.resource
                new_resource.lease.resource = resource.lease.resource
                new_resource.lease.epoch = resource.lease.epoch

                for seq in resource.lease.sequence:
                    new_resource.lease.sequence.append(seq)

                new_resource.lease_owner.client_name = resource.lease_owner.client_name
                new_resource.lease_owner.user_name = resource.lease_owner.user_name

                lease_array_msg.resources.append(new_resource)

            self.lease_pub.publish(lease_array_msg)

    def FrontImageCB(self, results):
        """Callback for when the Spot Wrapper gets new front image data.
        Args:
            results: FutureWrapper object of AsyncPeriodicQuery callback
        """
        data = self.spot_wrapper.front_images
        if data:
            image_msg0, camera_info_msg0 = getImageMsg(data[0], self.spot_wrapper)
            self.frontleft_image_pub.publish(image_msg0)
            self.frontleft_image_info_pub.publish(camera_info_msg0)
            image_msg1, camera_info_msg1 = getImageMsg(data[1], self.spot_wrapper)
            self.frontright_image_pub.publish(image_msg1)
            self.frontright_image_info_pub.publish(camera_info_msg1)
            image_msg2, camera_info_msg2 = getImageMsg(data[2], self.spot_wrapper)
            self.frontleft_depth_pub.publish(image_msg2)
            self.frontleft_depth_info_pub.publish(camera_info_msg2)
            image_msg3, camera_info_msg3 = getImageMsg(data[3], self.spot_wrapper)
            self.frontright_depth_pub.publish(image_msg3)
            self.frontright_depth_info_pub.publish(camera_info_msg3)

            self.populate_camera_static_transforms(data[0])
            self.populate_camera_static_transforms(data[1])
            self.populate_camera_static_transforms(data[2])
            self.populate_camera_static_transforms(data[3])

    def SideImageCB(self, results):
        """Callback for when the Spot Wrapper gets new side image data.
        Args:
            results: FutureWrapper object of AsyncPeriodicQuery callback
        """
        data = self.spot_wrapper.side_images
        if data:
            image_msg0, camera_info_msg0 = getImageMsg(data[0], self.spot_wrapper)
            self.left_image_pub.publish(image_msg0)
            self.left_image_info_pub.publish(camera_info_msg0)
            image_msg1, camera_info_msg1 = getImageMsg(data[1], self.spot_wrapper)
            self.right_image_pub.publish(image_msg1)
            self.right_image_info_pub.publish(camera_info_msg1)
            image_msg2, camera_info_msg2 = getImageMsg(data[2], self.spot_wrapper)
            self.left_depth_pub.publish(image_msg2)
            self.left_depth_info_pub.publish(camera_info_msg2)
            image_msg3, camera_info_msg3 = getImageMsg(data[3], self.spot_wrapper)
            self.right_depth_pub.publish(image_msg3)
            self.right_depth_info_pub.publish(camera_info_msg3)

            self.populate_camera_static_transforms(data[0])
            self.populate_camera_static_transforms(data[1])
            self.populate_camera_static_transforms(data[2])
            self.populate_camera_static_transforms(data[3])

    def RearImageCB(self, results):
        """Callback for when the Spot Wrapper gets new rear image data.
        Args:
            results: FutureWrapper object of AsyncPeriodicQuery callback
        """
        data = self.spot_wrapper.rear_images
        if data:
            mage_msg0, camera_info_msg0 = getImageMsg(data[0], self.spot_wrapper)
            self.back_image_pub.publish(mage_msg0)
            self.back_image_info_pub.publish(camera_info_msg0)
            mage_msg1, camera_info_msg1 = getImageMsg(data[1], self.spot_wrapper)
            self.back_depth_pub.publish(mage_msg1)
            self.back_depth_info_pub.publish(camera_info_msg1)

            self.populate_camera_static_transforms(data[0])
            self.populate_camera_static_transforms(data[1])

    def handle_claim(self, request, response):
        """ROS service handler for the claim service"""
        resp = self.spot_wrapper.claim()
        response.success = True
        response.message = "hallo"
        print(response)
        return response

    def handle_release(self, req):
        """ROS service handler for the release service"""
        resp = self.spot_wrapper.release()
        return TriggerResponse(resp[0], resp[1])

    def handle_stop(self, req):
        """ROS service handler for the stop service"""
        resp = self.spot_wrapper.stop()
        return TriggerResponse(resp[0], resp[1])

    def handle_self_right(self, req):
        """ROS service handler for the self-right service"""
        resp = self.spot_wrapper.self_right()
        return TriggerResponse(resp[0], resp[1])

    def handle_sit(self, req):
        """ROS service handler for the sit service"""
        resp = self.spot_wrapper.sit()
        return TriggerResponse(resp[0], resp[1])

    def handle_stand(self, req):
        """ROS service handler for the stand service"""
        resp = self.spot_wrapper.stand()
        return TriggerResponse(resp[0], resp[1])

    def handle_power_on(self, req):
        """ROS service handler for the power-on service"""
        resp = self.spot_wrapper.power_on()
        return TriggerResponse(resp[0], resp[1])

    def handle_safe_power_off(self, req):
        """ROS service handler for the safe-power-off service"""
        resp = self.spot_wrapper.safe_power_off()
        return TriggerResponse(resp[0], resp[1])

    def handle_estop_hard(self, req):
        """ROS service handler to hard-eStop the robot.  The robot will immediately cut power to the motors"""
        resp = self.spot_wrapper.assertEStop(True)
        return TriggerResponse(resp[0], resp[1])

    def handle_estop_soft(self, req):
        """ROS service handler to soft-eStop the robot.  The robot will try to settle on the ground before cutting
        power to the motors """
        resp = self.spot_wrapper.assertEStop(False)
        return TriggerResponse(resp[0], resp[1])

    def handle_estop_disengage(self, req):
        """ROS service handler to disengage the eStop on the robot."""
        resp = self.spot_wrapper.disengageEStop()
        return TriggerResponse(resp[0], resp[1])

    def handle_clear_behavior_fault(self, req, resp):
        """ROS service handler for clearing behavior faults"""
        resp = self.spot_wrapper.clear_behavior_fault(req.id)
        return ClearBehaviorFaultResponse(resp[0], resp[1])

    def handle_stair_mode(self, req):
        """ROS service handler to set a stair mode to the robot."""
        try:
            mobility_params = self.spot_wrapper.get_mobility_params()
            mobility_params.stair_hint = req.data
            self.spot_wrapper.set_mobility_params( mobility_params )
            return SetBoolResponse(True, 'Success')
        except Exception as e:
            return SetBoolResponse(False, 'Error:{}'.format(e))

    def handle_locomotion_mode(self, req):
        """ROS service handler to set locomotion mode"""
        try:
            mobility_params = self.spot_wrapper.get_mobility_params()
            mobility_params.locomotion_hint = req.locomotion_mode
            self.spot_wrapper.set_mobility_params( mobility_params )
            return SetLocomotionResponse(True, 'Success')
        except Exception as e:
            return SetLocomotionResponse(False, 'Error:{}'.format(e))

    def handle_max_vel(self, req):
        """
        Handle a max_velocity service call. This will modify the mobility params to have a limit on the maximum
        velocity that the robot can move during motion commmands. This affects trajectory commands and velocity
        commands
        Args:
            req: SetVelocityRequest containing requested maximum velocity
        Returns: SetVelocityResponse
        """
        try:
            mobility_params = self.spot_wrapper.get_mobility_params()
            mobility_params.vel_limit.CopyFrom(SE2VelocityLimit(max_vel=math_helpers.SE2Velocity(req.velocity_limit.linear.x,
                                                                                                 req.velocity_limit.linear.y,
                                                                                                 req.velocity_limit.angular.z).to_proto()))
            self.spot_wrapper.set_mobility_params(mobility_params)
            return SetVelocityResponse(True, 'Success')
        except Exception as e:
            return SetVelocityResponse(False, 'Error:{}'.format(e))

    def handle_trajectory(self, req):
        """ROS actionserver execution handler to handle receiving a request to move to a location"""
        if req.target_pose.header.frame_id != 'body':
            self.trajectory_server.set_aborted(TrajectoryResult(False, 'frame_id of target_pose must be \'body\''))
            return
        if req.duration.data.to_sec() <= 0:
            self.trajectory_server.set_aborted(TrajectoryResult(False, 'duration must be larger than 0'))
            return

        cmd_duration = rclpy.Duration(req.duration.data.secs, req.duration.data.nsecs)
        resp = self.spot_wrapper.trajectory_cmd(
                        goal_x=req.target_pose.pose.position.x,
                        goal_y=req.target_pose.pose.position.y,
                        goal_heading=math_helpers.Quat(
                            w=req.target_pose.pose.orientation.w,
                            x=req.target_pose.pose.orientation.x,
                            y=req.target_pose.pose.orientation.y,
                            z=req.target_pose.pose.orientation.z
                            ).to_yaw(),
                        cmd_duration=cmd_duration.to_sec(),
                        precise_position=req.precise_positioning,
                        )

        def timeout_cb(trajectory_server, _):
            trajectory_server.publish_feedback(TrajectoryFeedback("Failed to reach goal, timed out"))
            trajectory_server.set_aborted(TrajectoryResult(False, "Failed to reach goal, timed out"))

        # Abort the actionserver if cmd_duration is exceeded - the driver stops but does not provide feedback to
        # indicate this so we monitor it ourselves
        cmd_timeout = rclpy.Timer(cmd_duration, functools.partial(timeout_cb, self.trajectory_server), oneshot=True)

        # The trajectory command is non-blocking but we need to keep this function up in order to interrupt if a
        # preempt is requested and to return success if/when the robot reaches the goal. Also check the is_active to
        # monitor whether the timeout_cb has already aborted the command
        rate = rclpy.Rate(10)
        while not rclpy.is_shutdown() and not self.trajectory_server.is_preempt_requested() and not self.spot_wrapper.at_goal and self.trajectory_server.is_active():
            if self.spot_wrapper.near_goal:
                if self.spot_wrapper._last_trajectory_command_precise:
                    self.trajectory_server.publish_feedback(TrajectoryFeedback("Near goal, performing final adjustments"))
                else:
                    self.trajectory_server.publish_feedback(TrajectoryFeedback("Near goal"))
            else:
                self.trajectory_server.publish_feedback(TrajectoryFeedback("Moving to goal"))
            rate.sleep()

        # If still active after exiting the loop, the command did not time out
        if self.trajectory_server.is_active():
            cmd_timeout.shutdown()
            if self.trajectory_server.is_preempt_requested():
                self.trajectory_server.publish_feedback(TrajectoryFeedback("Preempted"))
                self.trajectory_server.set_preempted()
                self.spot_wrapper.stop()

            if self.spot_wrapper.at_goal:
                self.trajectory_server.publish_feedback(TrajectoryFeedback("Reached goal"))
                self.trajectory_server.set_succeeded(TrajectoryResult(resp[0], resp[1]))
            else:
                self.trajectory_server.publish_feedback(TrajectoryFeedback("Failed to reach goal"))
                self.trajectory_server.set_aborted(TrajectoryResult(False, "Failed to reach goal"))

    def cmdVelCallback(self, data):
        """Callback for cmd_vel command"""
        self.spot_wrapper.velocity_cmd(data.linear.x, data.linear.y, data.angular.z)

    def bodyPoseCallback(self, data):
        """Callback for cmd_vel command"""
        q = Quaternion()
        q.x = data.orientation.x
        q.y = data.orientation.y
        q.z = data.orientation.z
        q.w = data.orientation.w
        position = geometry_pb2.Vec3(z=data.position.z)
        pose = geometry_pb2.SE3Pose(position=position, rotation=q)
        point = trajectory_pb2.SE3TrajectoryPoint(pose=pose)
        traj = trajectory_pb2.SE3Trajectory(points=[point])
        body_control = spot_command_pb2.BodyControlParams(base_offset_rt_footprint=traj)

        mobility_params = self.spot_wrapper.get_mobility_params()
        mobility_params.body_control.CopyFrom(body_control)
        self.spot_wrapper.set_mobility_params(mobility_params)

    def handle_list_graph(self, upload_path):
        """ROS service handler for listing graph_nav waypoint_ids"""
        resp = self.spot_wrapper.list_graph(upload_path)
        return ListGraphResponse(resp)

    def handle_navigate_to_feedback(self):
        """Thread function to send navigate_to feedback"""
        while rclpy.ok() and self.run_navigate_to:
            localization_state = self.spot_wrapper._graph_nav_client.get_localization_state()
            if localization_state.localization.waypoint_id:
                self.navigate_as.publish_feedback(NavigateToFeedback(localization_state.localization.waypoint_id))
            rclpy.Rate(10).sleep()

    def handle_navigate_to(self, msg):
        """ROS service handler to run mission of the robot.  The robot will replay a mission"""
        # create thread to periodically publish feedback
        feedback_thraed = threading.Thread(target = self.handle_navigate_to_feedback, args = ())
        self.run_navigate_to = True
        feedback_thraed.start()
        # run navigate_to
        resp = self.spot_wrapper.navigate_to(upload_path = msg.upload_path,
                                             navigate_to = msg.navigate_to,
                                             initial_localization_fiducial = msg.initial_localization_fiducial,
                                             initial_localization_waypoint = msg.initial_localization_waypoint)
        self.run_navigate_to = False
        feedback_thraed.join()

        # check status
        if resp[0]:
            self.navigate_as.set_succeeded(NavigateToResult(resp[0], resp[1]))
        else:
            self.navigate_as.set_aborted(NavigateToResult(resp[0], resp[1]))

    def populate_camera_static_transforms(self, image_data):
        """Check data received from one of the image tasks and use the transform snapshot to extract the camera frame
        transforms. This is the transforms from body->frontleft->frontleft_fisheye, for example. These transforms
        never change, but they may be calibrated slightly differently for each robot so we need to generate the
        transforms at runtime.
        Args:
        image_data: Image protobuf data from the wrapper
        """
        # We exclude the odometry frames from static transforms since they are not static. We can ignore the body
        # frame because it is a child of odom or vision depending on the mode_parent_odom_tf, and will be published
        # by the non-static transform publishing that is done by the state callback
        excluded_frames = [self.tf_name_vision_odom, self.tf_name_kinematic_odom, "body"]
        for frame_name in image_data.shot.transforms_snapshot.child_to_parent_edge_map:
            if frame_name in excluded_frames:
                continue
            parent_frame = image_data.shot.transforms_snapshot.child_to_parent_edge_map.get(frame_name).parent_frame_name
            existing_transforms = [(transform.header.frame_id, transform.child_frame_id) for transform in self.camera_static_transforms]
            if (parent_frame, frame_name) in existing_transforms:
                # We already extracted this transform
                continue

            transform = image_data.shot.transforms_snapshot.child_to_parent_edge_map.get(frame_name)
            local_time = self.spot_wrapper.robotToLocalTime(image_data.shot.acquisition_time)
            tf_time = Time(local_time.seconds, local_time.nanos)
            static_tf = populateTransformStamped(tf_time, transform.parent_frame_name, frame_name,
                                                 transform.parent_tform_child)
            self.camera_static_transforms.append(static_tf)
            self.camera_static_transform_broadcaster.sendTransform(self.camera_static_transforms)

    def shutdown(self, sig, frame):
        self.node.get_logger().info("Shutting down ROS driver for Spot")
        self.spot_wrapper.sit()
        self.node_rate.sleep()
        self.spot_wrapper.disconnect()

    def step(self):
        """ Update spot sensors """
        self.node.get_logger().info("Step/Update")
        while rclpy.ok():
            self.spot_wrapper.updateTasks() ############## testing with Robot
            #self.node.get_logger().info("UPDATE TASKS")
            feedback_msg = Feedback()
            feedback_msg.standing = self.spot_wrapper.is_standing
            feedback_msg.sitting = self.spot_wrapper.is_sitting
            feedback_msg.moving = self.spot_wrapper.is_moving
            id = self.spot_wrapper.id
            try:
                feedback_msg.serial_number = id.serial_number
                feedback_msg.species = id.species
                feedback_msg.version = id.version
                feedback_msg.nickname = id.nickname
                feedback_msg.computer_serial_number = id.computer_serial_number
            except:
                pass
            self.feedback_pub.publish(feedback_msg)
            mobility_params_msg = MobilityParams()
            try:
                mobility_params = self.spot_wrapper.get_mobility_params()
                mobility_params_msg.body_control.position.x = \
                        mobility_params.body_control.base_offset_rt_footprint.points[0].pose.position.x
                mobility_params_msg.body_control.position.y = \
                        mobility_params.body_control.base_offset_rt_footprint.points[0].pose.position.y
                mobility_params_msg.body_control.position.z = \
                        mobility_params.body_control.base_offset_rt_footprint.points[0].pose.position.z
                mobility_params_msg.body_control.orientation.x = \
                        mobility_params.body_control.base_offset_rt_footprint.points[0].pose.rotation.x
                mobility_params_msg.body_control.orientation.y = \
                        mobility_params.body_control.base_offset_rt_footprint.points[0].pose.rotation.y
                mobility_params_msg.body_control.orientation.z = \
                        mobility_params.body_control.base_offset_rt_footprint.points[0].pose.rotation.z
                mobility_params_msg.body_control.orientation.w = \
                        mobility_params.body_control.base_offset_rt_footprint.points[0].pose.rotation.w
                mobility_params_msg.locomotion_hint = mobility_params.locomotion_hint
                mobility_params_msg.stair_hint = mobility_params.stair_hint
            except Exception as e:
                self.node.get_logger().error('Error:{}'.format(e))
                pass
            self.mobility_params_pub.publish(mobility_params_msg)
            self.node_rate.sleep()

def main(args = None):
    print('Hi from spot_driver.')
    spot_ros = SpotROS()
    rclpy.init(args=args)
    """Main function for the SpotROS class.  Gets config from ROS and initializes the wrapper.  Holds lease from wrapper and updates all async tasks at the ROS rate"""
    
    node = rclpy.create_node('spot_ros2')

    spot_ros.node = node
    rate = node.create_rate(50)
    spot_ros.node_rate = rate

    # spot_ros.yaml
    spot_ros.rates = {}
    spot_ros.rates['robot_state'] = 20.0
    spot_ros.rates['metrics'] = 0.04
    spot_ros.rates['lease'] = 1.0
    spot_ros.rates['front_image'] = 10.0
    spot_ros.rates['side_image'] = 10.0
    spot_ros.rates['rear_image'] = 10.0

    node.declare_parameter('auto_claim')
    node.declare_parameter('auto_power_on')
    node.declare_parameter('auto_stand')

    node.declare_parameter('deadzone')
    node.declare_parameter('estop_timeout')

    # spot_login.yaml
    node.declare_parameter('username')
    node.declare_parameter('password')
    node.declare_parameter('hostname')
    
    spot_ros.auto_claim = node.get_parameter('auto_claim')
    spot_ros.auto_power_on = node.get_parameter('auto_power_on')
    spot_ros.auto_stand = node.get_parameter('auto_stand')

    spot_ros.motion_deadzone = node.get_parameter('deadzone')
    spot_ros.estop_timeout = node.get_parameter('estop_timeout')


    spot_ros.username = node.get_parameter('username')
    spot_ros.password = node.get_parameter('password')
    spot_ros.hostname = node.get_parameter('hostname')

    # New vars for spot login; ros params not working in debug
    hostname = "10.0.0.3"
    username = "admin"
    password = "cjck5eaph39s"
    print("login: "+str(username)+" "+str(password)+" "+str(hostname))

    spot_ros.camera_static_transform_broadcaster = tf2_ros.StaticTransformBroadcaster(node)
    # Static transform broadcaster is super simple and just a latched publisher. Every time we add a new static
    # transform we must republish all static transforms from this source, otherwise the tree will be incomplete.
    # We keep a list of all the static transforms we already have so they can be republished, and so we can check
    # which ones we already have
    spot_ros.camera_static_transforms = []

    # Spot has 2 types of odometries: 'odom' and 'vision'
    # The former one is kinematic odometry and the second one is a combined odometry of vision and kinematics
    # These params enables to change which odometry frame is a parent of body frame and to change tf names of each odometry frames.
    spot_ros.mode_parent_odom_tf = node.declare_parameter('mode_parent_odom_tf', 'odom') # 'vision' or 'odom'
    spot_ros.tf_name_kinematic_odom = node.declare_parameter('tf_name_kinematic_odom', 'odom')
    spot_ros.tf_name_raw_kinematic = 'odom'
    spot_ros.tf_name_vision_odom = node.declare_parameter('tf_name_vision_odom', 'vision')
    spot_ros.tf_name_raw_vision = 'vision'

    if spot_ros.mode_parent_odom_tf.value != spot_ros.tf_name_raw_kinematic and spot_ros.mode_parent_odom_tf.value != spot_ros.tf_name_raw_vision:
        node.get_logger().error('rosparam \'mode_parent_odom_tf\' should be \'odom\' or \'vision\'.')
        return

    # logger
    spot_ros.logger = logging.getLogger('rosout')
    node.get_logger().info("Starting ROS driver for Spot")
    ############## testing with Robot
    spot_ros.spot_wrapper = SpotWrapper(spot_ros.username, spot_ros.password, spot_ros.hostname, spot_ros.logger, spot_ros.estop_timeout.value, spot_ros.rates, spot_ros.callbacks)
    # spot_ros.spot_wrapper = spot_wrapper
    if  spot_ros.spot_wrapper.is_valid:
        # Images #
        spot_ros.back_image_pub = node.create_publisher(Image, 'camera/back/image', 1)
        spot_ros.frontleft_image_pub = node.create_publisher(Image, 'camera/frontleft/image', 1)
        spot_ros.frontright_image_pub = node.create_publisher(Image, 'camera/frontright/image', 1)
        spot_ros.left_image_pub = node.create_publisher(Image, 'camera/left/image', 1)
        spot_ros.right_image_pub = node.create_publisher(Image, 'camera/right/image', 1)
        # Depth #
        spot_ros.back_depth_pub = node.create_publisher(Image, 'depth/back/image', 1)
        spot_ros.frontleft_depth_pub = node.create_publisher(Image, 'depth/frontleft/image', 1)
        spot_ros.frontright_depth_pub = node.create_publisher(Image, 'depth/frontright/image', 1)
        spot_ros.left_depth_pub = node.create_publisher(Image, 'depth/left/image', 1)
        spot_ros.right_depth_pub = node.create_publisher(Image, 'depth/right/image', 1)

        # Image Camera Info #
        spot_ros.back_image_info_pub = node.create_publisher(CameraInfo, 'camera/back/camera_info', 1)
        spot_ros.frontleft_image_info_pub = node.create_publisher(CameraInfo, 'camera/frontleft/camera_info', 1)
        spot_ros.frontright_image_info_pub = node.create_publisher(CameraInfo, 'camera/frontright/camera_info', 1)
        spot_ros.left_image_info_pub = node.create_publisher(CameraInfo, 'camera/left/camera_info', 1)
        spot_ros.right_image_info_pub = node.create_publisher(CameraInfo, 'camera/right/camera_info', 1)
        # Depth Camera Info #
        spot_ros.back_depth_info_pub = node.create_publisher(CameraInfo,'depth/back/camera_info', 1)
        spot_ros.frontleft_depth_info_pub = node.create_publisher(CameraInfo, 'depth/frontleft/camera_info', 1)
        spot_ros.frontright_depth_info_pub = node.create_publisher(CameraInfo, 'depth/frontright/camera_info', 1)
        spot_ros.left_depth_info_pub = node.create_publisher(CameraInfo, 'depth/left/camera_info', 1)
        spot_ros.right_depth_info_pub = node.create_publisher(CameraInfo, 'depth/right/camera_info', 1)

        # Status Publishers #
        spot_ros.joint_state_pub = node.create_publisher(JointState, 'joint_states', 1)
        """Defining a TF publisher manually because of conflicts between Python3 and tf"""
        spot_ros.tf_pub = node.create_publisher(TFMessage,'tf', 1)
        spot_ros.metrics_pub = node.create_publisher(Metrics, 'status/metrics', 1)
        spot_ros.lease_pub = node.create_publisher(LeaseArray, 'status/leases', 1)
        spot_ros.odom_twist_pub = node.create_publisher(TwistWithCovarianceStamped, 'odometry/twist', 1)
        spot_ros.odom_pub = node.create_publisher(Odometry, 'odometry', 1)
        spot_ros.feet_pub = node.create_publisher(FootStateArray, 'status/feet', 1)
        spot_ros.estop_pub = node.create_publisher(EStopStateArray, 'status/estop', 1)
        spot_ros.wifi_pub = node.create_publisher(WiFiState, 'status/wifi', 1)
        spot_ros.power_pub = node.create_publisher(PowerState, 'status/power_state', 1)
        spot_ros.battery_pub = node.create_publisher(BatteryStateArray, 'status/battery_states', 1)
        spot_ros.behavior_faults_pub = node.create_publisher(BehaviorFaultState, 'status/behavior_faults', 1)
        spot_ros.system_faults_pub = node.create_publisher(SystemFaultState, 'status/system_faults', 1)

        spot_ros.feedback_pub = node.create_publisher(Feedback, 'status/feedback', 1)

        spot_ros.mobility_params_pub = node.create_publisher(MobilityParams, 'status/mobility_params', 1)

        node.create_subscription(Twist, 'cmd_vel', spot_ros.cmdVelCallback, 1)
        node.create_subscription(Pose, 'body_pose', spot_ros.bodyPoseCallback, 1)
        node.create_service(Trigger, 'claim', spot_ros.handle_claim)
        node.create_service(Trigger, 'release', spot_ros.handle_release)       
        node.create_service(Trigger, "stop", spot_ros.handle_stop)
        node.create_service(Trigger, "self_right", spot_ros.handle_self_right)
        node.create_service(Trigger, "sit", spot_ros.handle_sit)
        node.create_service(Trigger, "stand", spot_ros.handle_stand)
        node.create_service(Trigger, "power_on", spot_ros.handle_power_on)
        node.create_service(Trigger, "power_off", spot_ros.handle_safe_power_off)
        node.create_service(Trigger,"estop/hard", spot_ros.handle_estop_hard)
        node.create_service(Trigger,"estop/gentle", spot_ros.handle_estop_soft)
        node.create_service(Trigger,"estop/release", spot_ros.handle_estop_disengage)

        node.create_service(SetBool, "stair_mode", spot_ros.handle_stair_mode)
        node.create_service(SetLocomotion, "locomotion_mode", spot_ros.handle_locomotion_mode)
        node.create_service(SetVelocity, "max_velocity", spot_ros.handle_max_vel)
        node.create_service(ClearBehaviorFault, "clear_behavior_fault", spot_ros.handle_clear_behavior_fault)

        node.create_service(ListGraph, "list_graph", spot_ros.handle_list_graph)
        
        spot_ros.navigate_as = ActionServer(node, NavigateTo, 'navigate_to', spot_ros.handle_navigate_to)
        #spot_ros.navigate_as.start() # As is online

        spot_ros.trajectory_server = ActionServer(node, Trajectory, 'trajectory', spot_ros.handle_trajectory)
        #spot_ros.trajectory_server.start()
        
        # Register Shutdown Handle
        # rclpy.on_shutdown(spot_ros.shutdown) ############## Shutdown Handle
        #print(str(spot_ros.auto_claim.value)+" "+str(spot_ros.auto_power_on.value)+" "+str(spot_ros.auto_stand.value))
        #spot_ros.auto_claim = rclpy.get_param('~auto_claim', )
        #spot_ros.auto_power_on = rclpy.get_param('~auto_power_on', False)
        #spot_ros.auto_stand = rclpy.get_param('~auto_stand', False)

        if spot_ros.auto_claim.value:
            spot_ros.spot_wrapper.claim()
            if spot_ros.auto_power_on.value:
                spot_ros.spot_wrapper.power_on()
                if spot_ros.auto_stand.value:
                    spot_ros.spot_wrapper.stand()
        sys.stdout.flush()
        update_thraed = threading.Thread(target = spot_ros.step, args = ())
        update_thraed.start()
        signal.signal(signal.SIGTERM, spot_ros.shutdown)

        rclpy.spin(node)
        node.get_logger().info("Shutdown")
        ## Spot shutdown handle; disconnect spot
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()