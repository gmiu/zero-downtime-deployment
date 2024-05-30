import pytest
import os

MOCK_REGION = 'us-east-1'

@pytest.fixture(scope="function")
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

# The following fixtures are used to mock AWS services using Moto
@pytest.fixture(scope="function")
def ec2_client(aws_credentials):
    import boto3
    from moto import mock_aws

    with mock_aws():
        yield boto3.client("ec2", region_name=MOCK_REGION)

@pytest.fixture(scope="function")
def autoscaling_client(aws_credentials):
    import boto3
    from moto import mock_aws

    with mock_aws():
        yield boto3.client("autoscaling", region_name=MOCK_REGION)

@pytest.fixture(scope="function")
def aws_helper(aws_credentials, ec2_client, autoscaling_client):
    from deployment import aws_helpers

    helpers = aws_helpers.AWSHelper(MOCK_REGION)
    helpers.ec2_client = ec2_client
    helpers.autoscaling_client = autoscaling_client

    return helpers

@pytest.fixture(scope="function")
def ami_id(ec2_client):
    ret = {}
    for version in ['v1', 'v2']:
        response = ec2_client.register_image(
            Name=f'test-ami-{version}',
            Architecture='x86_64',
            RootDeviceName='/dev/sda1',
            BlockDeviceMappings=[
                {
                    'DeviceName': '/dev/sda1',
                    'Ebs': {
                        'VolumeSize': 8
                    }
                }
            ]
        )
        ret[version] = response['ImageId']

    return ret

@pytest.fixture(scope="function")
def launch_template_id(ec2_client, ami_id):
    response = ec2_client.create_launch_template(
        LaunchTemplateName='test-launch-template',
        LaunchTemplateData={
            'ImageId': ami_id['v1'],
            'InstanceType': 't2.micro',
            'KeyName': 'test-key',
            'SecurityGroupIds': ['sg-123456']
        }
    )

    return response['LaunchTemplate']['LaunchTemplateId']

# The following tests are used to test the AWSHelper class
def test_get_current_asg_details_found(aws_helper, launch_template_id):
    asg_details = {
        'AutoScalingGroupName': 'test-asg',
        'LaunchTemplate': {
            'LaunchTemplateId': launch_template_id,
            'Version': '$Latest'
        },
        'AvailabilityZones': ['us-east-1a'],
        'DesiredCapacity': 1,
        'MinSize': 1,
        'MaxSize': 1
    }

    aws_helper.autoscaling_client.create_auto_scaling_group(**asg_details)

    response = aws_helper.get_current_asg_details(asg_details['AutoScalingGroupName'])
    assert response['AutoScalingGroupName'] == asg_details['AutoScalingGroupName']
    assert response['LaunchTemplate']['LaunchTemplateId'] == launch_template_id
    assert response['DesiredCapacity'] == asg_details['DesiredCapacity']
    assert response['MinSize'] == asg_details['MinSize']
    assert response['MaxSize'] == asg_details['MaxSize']

def test_get_current_asg_details_not_found(aws_helper):
    with pytest.raises(ValueError):
        aws_helper.get_current_asg_details('test-asg')

def test_get_current_ami_id_found(aws_helper, launch_template_id, ami_id):
    aws_helper.ec2_client.create_launch_template_version(
        LaunchTemplateId=launch_template_id,
        LaunchTemplateData={
            'ImageId': ami_id['v1']
        }
    )

    current_ami_id = aws_helper.get_current_ami_id(launch_template_id, '$Latest')
    assert current_ami_id == ami_id['v1']

    aws_helper.ec2_client.create_launch_template_version(
        LaunchTemplateId=launch_template_id,
        LaunchTemplateData={
            'ImageId': ami_id['v2']
        }
    )
    current_ami_id = aws_helper.get_current_ami_id(launch_template_id, '$Latest')
    assert current_ami_id == ami_id['v2']

def test_get_current_ami_id_not_found(aws_helper, launch_template_id):
    with pytest.raises(ValueError):
        aws_helper.get_current_ami_id(launch_template_id, 'v1')

def test_update_auto_scaling_group_no_change(aws_helper, launch_template_id, ami_id):
    asg_details = {
        'AutoScalingGroupName': 'test-asg',
        'LaunchTemplate': {
            'LaunchTemplateId': launch_template_id,
            'Version': '$Latest'
        },
        'AvailabilityZones': ['us-east-1a'],
        'DesiredCapacity': 1,
        'MinSize': 1,
        'MaxSize': 1
    }

    aws_helper.ec2_client.create_launch_template_version(
        LaunchTemplateId=launch_template_id,
        LaunchTemplateData={
            'ImageId': ami_id['v1']
        }
    )

    aws_helper.autoscaling_client.create_auto_scaling_group(**asg_details)

    new_version = aws_helper.update_auto_scaling_group(asg_details, ami_id['v1'], 1, 1, 1)
    assert new_version is None

def test_update_auto_scaling_group_change(aws_helper, launch_template_id, ami_id):
    asg_details = {
        'AutoScalingGroupName': 'test-asg',
        'LaunchTemplate': {
            'LaunchTemplateId': launch_template_id,
            'Version': '$Latest'
        },
        'AvailabilityZones': ['us-east-1a'],
        'DesiredCapacity': 1,
        'MinSize': 1,
        'MaxSize': 1
    }

    aws_helper.ec2_client.create_launch_template_version(
        LaunchTemplateId=launch_template_id,
        LaunchTemplateData={
            'ImageId': ami_id['v1']
        }
    )

    aws_helper.autoscaling_client.create_auto_scaling_group(**asg_details)

    new_version = aws_helper.update_auto_scaling_group(asg_details, ami_id['v2'], 2, 2, 2)
    assert new_version is not None

    response = aws_helper.ec2_client.describe_launch_template_versions(
        LaunchTemplateId=launch_template_id,
        Versions=['$Latest']
    )
    assert response['LaunchTemplateVersions'][0]['LaunchTemplateData']['ImageId'] == ami_id['v2']

# def test_start_instance_refresh(aws_helper, launch_template_id, ami_id):
#     # placeholder for testing instance refresh
#     # Moto does not support mocking starting instance refresh yet
#     # NotImplementedError: The start_instance_refresh action has not been implemented
#     pass

# def test_wait_for_instance_refresh(aws_helper):
#     # placeholder for testing waiting for instance refresh
#     # Moto does not support mocking waiting for instance refresh yet
#     # NotImplementedError: The wait_for_instance_refresh action has not been implemented
#     pass
