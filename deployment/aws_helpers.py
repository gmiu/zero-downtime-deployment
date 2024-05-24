import boto3
import time
import logging
from botocore.exceptions import ClientError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

class AWSHelper:
    def __init__(self, region):
        self.region = region
        self.ec2_client = None
        self.autoscaling_client = None

    def initialize_aws_sdk(self):
        logging.info('Initializing AWS SDK')
        self.ec2_client = boto3.client('ec2', region_name=self.region)
        self.autoscaling_client = boto3.client('autoscaling', region_name=self.region)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(ClientError))
    def get_current_asg_details(self, asg_name):
        logging.info(f'Retrieving details for Auto Scaling Group: {asg_name}')
        try:
            response = self.autoscaling_client.describe_auto_scaling_groups(
                AutoScalingGroupNames=[asg_name]
            )
            if 'AutoScalingGroups' in response and len(response['AutoScalingGroups']) > 0:
                return response['AutoScalingGroups'][0]
            else:
                raise ValueError(f'Auto Scaling Group {asg_name} not found')
        except ClientError as e:
            logging.error(f'ClientError retrieving ASG details: {e}')
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(ClientError))
    def get_current_ami_id(self, launch_template_id, launch_template_version):
        logging.info(f'Retrieving current AMI ID from launch template {launch_template_id} version {launch_template_version}')
        try:
            response = self.ec2_client.describe_launch_template_versions(
                LaunchTemplateId=launch_template_id,
                Versions=[launch_template_version]
            )
            if 'LaunchTemplateVersions' in response and len(response['LaunchTemplateVersions']) > 0:
                return response['LaunchTemplateVersions'][0]['LaunchTemplateData']['ImageId']
            else:
                raise ValueError(f'Launch Template {launch_template_id} version {launch_template_version} not found')
        except ClientError as e:
            logging.error(f'ClientError retrieving AMI ID: {e}')
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(ClientError))
    def update_auto_scaling_group(self, asg_details, new_ami_id, desired_capacity, min_size, max_size):
        logging.info('Checking if Auto Scaling Group needs to be updated with new AMI or capacity settings')
        try:
            launch_template = asg_details['LaunchTemplate']
            launch_template_id = launch_template['LaunchTemplateId']
            launch_template_version = launch_template['Version']

            current_ami_id = self.get_current_ami_id(launch_template_id, launch_template_version)

            if current_ami_id == new_ami_id and asg_details['DesiredCapacity'] == desired_capacity and asg_details['MinSize'] == min_size and asg_details['MaxSize'] == max_size:
                logging.info('AMI ID, desired capacity, min size, and max size have not changed, no update required')
                return None

            logging.info('Updating Auto Scaling Group with new settings')
            update_params = {
                'AutoScalingGroupName': asg_details['AutoScalingGroupName'],
                'DesiredCapacity': desired_capacity,
                'MinSize': min_size,
                'MaxSize': max_size
            }

            if current_ami_id != new_ami_id:
                logging.info('AMI ID has changed, creating new version of the launch template')
                response = self.ec2_client.create_launch_template_version(
                    LaunchTemplateId=launch_template_id,
                    SourceVersion=launch_template_version,
                    LaunchTemplateData={
                        'ImageId': new_ami_id
                    }
                )
                new_version = response['LaunchTemplateVersion']['VersionNumber']

                update_params['LaunchTemplate'] = {
                    'LaunchTemplateId': launch_template_id,
                    'Version': str(new_version)
                }

            self.autoscaling_client.update_auto_scaling_group(**update_params)

            return new_version if 'new_version' in locals() else launch_template_version
        except ClientError as e:
            logging.error(f'ClientError updating Auto Scaling Group: {e}')
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(ClientError))
    def start_instance_refresh(self, asg_name, instance_refresh_config):
        logging.info(f'Starting instance refresh for ASG: {asg_name}')
        try:
            response = self.autoscaling_client.start_instance_refresh(
                AutoScalingGroupName=asg_name,
                Strategy='Rolling',
                Preferences={
                    'MinHealthyPercentage': instance_refresh_config['min_healthy_percentage'],
                    'MaxHealthyPercentage': instance_refresh_config['max_healthy_percentage'],
                    'InstanceWarmup': instance_refresh_config['instance_warmup'],
                    'SkipMatching': instance_refresh_config['skip_matching']
                }
            )
            return response['InstanceRefreshId']
        except ClientError as e:
            logging.error(f'ClientError starting instance refresh: {e}')
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(ClientError))
    def wait_for_instance_refresh(self, asg_name, instance_refresh_id):
        logging.info(f'Waiting for instance refresh {instance_refresh_id} to complete for ASG: {asg_name}')
        try:
            while True:
                response = self.autoscaling_client.describe_instance_refreshes(
                    AutoScalingGroupName=asg_name,
                    InstanceRefreshIds=[instance_refresh_id]
                )
                status = response['InstanceRefreshes'][0]['Status']
                if status in ['Successful', 'Failed', 'Cancelled']:
                    logging.info(f'Instance refresh {status}')
                    break
                logging.info('Instance refresh in progress...')
                time.sleep(10)  # Check the status more frequently
        except ClientError as e:
            logging.error(f'ClientError waiting for instance refresh: {e}')
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(ClientError))
    def verify_old_instances_termination(self, asg_name):
        logging.info(f'Verifying that old instances in ASG: {asg_name} are terminated')
        try:
            while True:
                response = self.autoscaling_client.describe_auto_scaling_instances()
                instances = response['AutoScalingInstances']
                old_instances = [
                    instance for instance in instances
                    if instance['AutoScalingGroupName'] == asg_name and
                    instance['LifecycleState'] != 'InService'
                ]
                if not old_instances:
                    logging.info('Old instances have been terminated')
                    break
                logging.info('Waiting for old instances to terminate...')
                for instance in old_instances:
                    self.ec2_client.terminate_instances(InstanceIds=[instance['InstanceId']])
                time.sleep(30)  # Adjust the interval as needed
        except ClientError as e:
            logging.error(f'ClientError verifying old instances termination: {e}')
            raise
