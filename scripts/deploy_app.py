import os
import yaml
import logging
from deployment.deployment import Deployment

def main():
    # Setup logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    # Load configuration file
    config_path = os.path.join(os.path.dirname(__file__), 'config', 'deployment_config.yaml')

    with open(config_path, 'r') as config_file:
        config = yaml.safe_load(config_file)

    # Initialize and run deployment
    deployment = Deployment(config)
    deployment.run()

if __name__ == "__main__":
    main()
