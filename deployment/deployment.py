import yaml
import os
import logging
from . import aws_helpers

class Deployment:
    def __init__(self, config):
        self.aws_helper = aws_helpers.AWSHelper(config['aws_region'])
        self.config = config
        self.new_ami_id = config['ami_id']
        self.desired_capacity = config['desired_capacity']
        self.min_size = config['min_size']
        self.max_size = config['max_size']
        self.instance_refresh_config = config['instance_refresh']
        self.setup_logging()

    def setup_logging(self):
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)

    def run(self):
        self.logger.info('Starting deployment process')
        self.aws_helper.initialize_aws_sdk()
        
        try:
            current_asg_details = self.aws_helper.get_current_asg_details(self.config['auto_scaling_group'])
            self.logger.info(f'ASG Details: {current_asg_details}')
        except ValueError as e:
            self.logger.error(f'Error retrieving ASG details: {e}')
            return
        
        try:
            new_version = self.aws_helper.update_auto_scaling_group(
                current_asg_details, self.new_ami_id, self.desired_capacity, self.min_size, self.max_size
            )
            if new_version is not None:
                self.logger.info(f'ASG updated to use new launch template version: {new_version}')
                
                instance_refresh_id = self.aws_helper.start_instance_refresh(self.config['auto_scaling_group'], self.instance_refresh_config)
                self.logger.info(f'Started instance refresh with ID: {instance_refresh_id}')
                
                self.aws_helper.wait_for_instance_refresh(self.config['auto_scaling_group'], instance_refresh_id)
                self.logger.info('Instance refresh completed successfully')

                self.aws_helper.verify_old_instances_termination(self.config['auto_scaling_group'])
                self.logger.info('Old instances have been terminated')
            else:
                self.logger.info('No update required, AMI ID, desired capacity, min size, and max size have not changed')
        except Exception as e:
            self.logger.error(f'Error updating ASG with new AMI: {e}')
            return
        
        self.logger.info('Deployment process completed successfully')
