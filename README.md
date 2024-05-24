# Zero downtime deployment POC

## Intro
We have an app deployed on a set of EC2 instances managed by an auto scaling group. Because this is a highly sensitive project and the our app availablitiy is crucial, the team decided to implement **blue/green deployments** in order to minimize the risk of downtime when a new version is deployed.

Here are some assumptions we are making:
- the version of **the app is shipped as new AMI**
- the build process happens when the new changes are merged into the main branch of the app project
- the infrastructure creation happens into a different project (or in the same if we also use the AWS SDK - anyway, it's irrelevant!)
- in this phase, **the POC doesn't take into consideration any database migrations**; it only updates the application that's running on the EC2 instances

## Pseudocode
Here is the pseudocode for the deployment:

```python
ACTIVE_ASG = blue_asg
STANDBY_ASG = green_asg
ACTIVE_AMI = ami_0.0.1
STANDBY_AMI = ami_0.0.2 # this is the new version
DESIRED_SIZE = X
MIN_SIZE = Y
MAX_SIZE = Z
MAIN_TG = arn_main_target_group
SYNTHETIC_TG = arn_synthetic_target_group
AUTO_ROLLBACK_TIME_WINDOW = 1 hour

# make sure there are no instances in the standby asg
scale_asg_to_zero(STANDBY_ASG)

update_asg(STANDBY_ASG, STANDBY_AMI, DESIRED_SIZE, MIN_SIZE, MAX_SIZE)
wait_for_asg_update(STANDBY_ASG)

attach_asg_to_target_group(STANDBY_ASG, SYNTHETIC_TG)

synthetic_result = run_synthetic_checks()

if synthetic_result == PASSED:
    # switch traffic to the standby asg
    attach_asg_to_target_group(STANDBY_ASG, MAIN_TG)
    put_asg_instances_in_standby(ACTIVE_ASG)

    # automatically run some monitoring and validations for the indicated time
    # at any time during the AUTO_ROLLBACK_TIME_WINDOW this can fail and initiate the rollback
    validation_result = monitor_and_validate(AUTO_ROLLBACK_TIME_WINDOW)

    if validation_result == PASSED:
        # remove the active asg from the main target group
        detach_asg_from_target_group(ACTIVE_ASG, MAIN_TG)
        detach_asg_from_target_group(STANDBY_ASG, synthetic_asg)
        scale_asg_to_zero(ACTIVE_ASG)

    else: # validation_result == FAILED
        # start auto rollback
        put_asg_instances_in_service(ACTIVE_ASG)
        detach_asg_from_target_group(STANDBY_ASG, MAIN_TG)

else: # synthetic_result == FAILED
    abort_deployment()
```

## Directory structure
Here is the directory structure:
```
project-root/
├── deployment/
│   ├── __init__.py
│   ├── deployment.py
│   └── aws_helpers.py
├── tests/
│   └── test_deployment
│       │── test_deployment.py
│       └── test_aws_helpers.py
├── scripts/
│   │── deploy_app.py
│   └── config/
│      └── deployment_config.yaml
├── README.md
└── requirements.txt
```

A few words on the directory structure:
- the `project_root` contains all sorts of modules that perform various infrastructure related stuff
- `deployment` is the module that contains the implementation of our zero downtime deployment
- `deployment/deployment.py` contains the orchestration of the steps described in the pseudocode
- `deployment/aws_helpers.py` contains the implementation of each step in `deployment/deployment.py` and is the place where all the AWS SDK calls happen
- `scripts/deploy_app.py` just imports the deployment module and starts the deployment
- `tests/` containts the tests for all of the modules, including `deployment`

## Discussion of the approach
Let's first describe our architecture:
- the app is running on EC2 instances managed by an ASG
- the ASG is attached to a main target group
- an ALB handles the production traffic and, by default, routes its to the main target group
- there is a secondary target group that is used for synthetic checks, called synthetic target group during the deployment, but more on that later

As mentioned in the intro, the zero downtime deployment is implemented as a blue/green deployment. This means that our infrastructure actually contains two auto scaling groups:

- the **blue** auto scaling group
- the **green** auto scaling group

Let's assume that for the deployment described in the approach:
- **the active ASG is the blue one** (which means the current version is running on the EC2 instances managed by the blue asg)
- **the standby ASG is the green one** (the new version will be deployed on the EC2 instances managed by the green asg) - when no deployments are happening, the standby ASG is scaled down to 0.

*Note: during the next version deployment, the active ASG will be the green ASG and the standby ASG will be the blue ASG. This is specified in the deployment config.*

There are 5 big steps in our deployment:
- deploy the new version on the standby ASG
- run synthetic checks
- shift traffic if synthetics passed
- monitor and validate
- rollback or finish the deployment

Let's take them one by one and discuss them briefly:

### Deploy the new version on the standby ASG
- first step is to make sure the standby ASG doesn't have any running instances that might run older versions (this steps is optional)
- then we update the standby ASG with the new version of the AMI and with the desired size and we wait for the for all the instances to be in service
- now we attach the standby ASG to the synthetic target group and move on to the next step

### Run synthetic checks
Now is the time to discuss how the synthetic checks are done. We needed a way to make sure the synthetic checks only reach the instances in the standby ASG.

The EC2 instances live in a private subnet and, chances are, we run our deployment from an entity that lives outside of that private network. So the easiest way for the synthetics to hit the EC2 instances is by hitting the ALB. If we were just to add the standby ASG to the main target group, the new instances would start receiving production traffic before the deployment is validated.

Here we introduce the synthetic target group to which we attach the standby ASG after the instances running the new version are ready.

**But how does the traffic gets routed to this target group?**

By introducing a new listener rule on the ALB that looks for an HTTP Header called **Synthetic** and its value must be **True**. Basically, if the **HTTP Header Synthetic is True** for a request, then that requests gets routed to the synthetic target group. All the synthetic checks include that HTTP Header and they get routed to the standby instances.

*As side a note, I did not validate this solution yet! :)*

### Shift traffic if the synthetic checks passed
But if they didn't, we just abort the deployment.

Now, let's consider everything is ok after running the synthetics. Here is what's happening:
- attach the standby ASG to the main target group; at this moment, both versions are serving traffic
- put the active ASG instances into standby; after this step, only the new version is serving traffic

### Monitor and validate

    # automatically run some monitoring and validations for the indicated time
    # at any time during the AUTO_ROLLBACK_TIME_WINDOW this can fail and initiate the rollback
    validation_result = monitor_and_validate(AUTO_ROLLBACK_TIME_WINDOW)

    if validation_result == PASSED:
        # remove the active asg from the main target group
        detach_asg_from_target_group(ACTIVE_ASG, MAIN_TG)
        detach_asg_from_target_group(STANDBY_ASG, synthetic_asg)
        scale_asg_to_zero(ACTIVE_ASG)
    else: # validation_result == FAILED
        # start auto rollback
        put_asg_instances_in_service(ACTIVE_ASG)
        detach_asg_from_target_group(STANDBY_ASG, MAIN_TG)

So, we are at the point when only the new version is serving traffic. We could just terminate the instances running the old version the scaling the active ASG to zero. Instead, we define a time period (AUTO_ROLLBACK_TIME_WINDOW) during which we monitor and validate that everything is ok. If any issue comes up during this time, the rollback is initiated automatically.

The monitoring and the validation step is automated in this case. This could mean running whatever test you have or subscribe the various alerting queues and if any critical alerts pop up, the deployment gets invalidated. Sky is the limit here!

After the AUTO_ROLLBACK_TIME_WINDOW expires, it's time to move on to the last step.

### Rollback of finish the deployment
If the AUTO_ROLLBACK_TIME_WINDOW expired and no issues were identified during this time, we move forward with the deployment. This basically means:
- detach the active ASG from the main target group
- detach the standby ASG from the synthetic target group
- scale down the active ASG to zero

That's it, the deployment is now complete. If any issues happen after the AUTO_ROLLBACK_TIME_WINDOW expires, the rollback should be done as a full deployment.

If any issues were identified during the AUTO_ROLLBACK_TIME_WINDOW, the monitoring and validation step is considered failed on the spot (no need to wait for the full AUTO_ROLLBACK_TIME_WINDOW) and the automatic rollback starts:
- put active ASG instances back in service
- detach the standby ASG from the main target group

Because we kept the old instances in standby, we can easily and rapidly reintroduce them into the rotation.

*Note: The use of active and standby after the deployment might be confusing, because the green deployment now becomes the active one and the blue becomes the standby. But I wanted to keep the exact terminology the pseudocode is using*

## Implemented parts
I implemented a rudimentary scenario with only one auto scaling group. No blue/green deployment or checkpoints used. You can find the implementation inside the deployment module. At the end, I pasted the logs of a failed run, due to an instance refresh already in progress ()

Lack of testing: unfortunately I did not have time to implement testing for the helper functions. I will probably update the repo with tests over the following days.

## Outro

That is it! It was a fun task. I wish I had the time to fully implement this.

## Some output samples

```
# this is a successful deployment
❯ python scripts/deploy_app.py

2024-05-24 16:21:56,620 - INFO - Starting deployment process
2024-05-24 16:21:56,620 - INFO - Initializing AWS SDK
2024-05-24 16:21:56,634 - INFO - Found credentials in environment variables.
2024-05-24 16:21:56,777 - INFO - Retrieving details for Auto Scaling Group: zdt-app-asg-blue
2024-05-24 16:21:57,357 - INFO - ASG Details: {'AutoScalingGroupName': 'zdt-app-asg-blue', 'AutoScalingGroupARN': 'arn:aws:autoscaling:us-east-1:197490477086:autoScalingGroup:5c98a12e-9aea-49a2-ae26-17bf20796724:autoScalingGroupName/zdt-app-asg-blue', 'LaunchTemplate': {'LaunchTemplateId': 'lt-0a408c46add7b1b37', 'LaunchTemplateName': 'zdt-app-template-blue-20240523210214573900000001', 'Version': '6'}, 'MinSize': 1, 'MaxSize': 4, 'DesiredCapacity': 3, 'DefaultCooldown': 300, 'AvailabilityZones': ['us-east-1a', 'us-east-1b'], 'LoadBalancerNames': [], 'TargetGroupARNs': ['arn:aws:elasticloadbalancing:us-east-1:197490477086:targetgroup/synthetic-tg/c4c386b4af8f61ce', 'arn:aws:elasticloadbalancing:us-east-1:197490477086:targetgroup/tf-20240523092733985500000006/8da2d1c8491abb21'], 'HealthCheckType': 'EC2', 'HealthCheckGracePeriod': 300, 'Instances': [{'InstanceId': 'i-0346648c83bba57c2', 'InstanceType': 't2.micro', 'AvailabilityZone': 'us-east-1b', 'LifecycleState': 'InService', 'HealthStatus': 'Healthy', 'LaunchTemplate': {'LaunchTemplateId': 'lt-0a408c46add7b1b37', 'LaunchTemplateName': 'zdt-app-template-blue-20240523210214573900000001', 'Version': '6'}, 'ProtectedFromScaleIn': False}, {'InstanceId': 'i-09488ac66580052c0', 'InstanceType': 't2.micro', 'AvailabilityZone': 'us-east-1a', 'LifecycleState': 'InService', 'HealthStatus': 'Healthy', 'LaunchTemplate': {'LaunchTemplateId': 'lt-0a408c46add7b1b37', 'LaunchTemplateName': 'zdt-app-template-blue-20240523210214573900000001', 'Version': '6'}, 'ProtectedFromScaleIn': False}, {'InstanceId': 'i-0ba2bdfb80e3b9af9', 'InstanceType': 't2.micro', 'AvailabilityZone': 'us-east-1a', 'LifecycleState': 'InService', 'HealthStatus': 'Healthy', 'LaunchTemplate': {'LaunchTemplateId': 'lt-0a408c46add7b1b37', 'LaunchTemplateName': 'zdt-app-template-blue-20240523210214573900000001', 'Version': '6'}, 'ProtectedFromScaleIn': False}], 'CreatedTime': datetime.datetime(2024, 5, 23, 20, 57, 10, 732000, tzinfo=tzutc()), 'SuspendedProcesses': [], 'VPCZoneIdentifier': 'subnet-0cc43bbe46640850a,subnet-0c5a8ae1a6f0b1c16', 'EnabledMetrics': [], 'Tags': [{'ResourceId': 'zdt-app-asg-blue', 'ResourceType': 'auto-scaling-group', 'Key': 'Name', 'Value': 'zdt-app', 'PropagateAtLaunch': True}], 'TerminationPolicies': ['Default'], 'NewInstancesProtectedFromScaleIn': False, 'ServiceLinkedRoleARN': 'arn:aws:iam::197490477086:role/aws-service-role/autoscaling.amazonaws.com/AWSServiceRoleForAutoScaling', 'TrafficSources': [{'Identifier': 'arn:aws:elasticloadbalancing:us-east-1:197490477086:targetgroup/synthetic-tg/c4c386b4af8f61ce', 'Type': 'elbv2'}, {'Identifier': 'arn:aws:elasticloadbalancing:us-east-1:197490477086:targetgroup/tf-20240523092733985500000006/8da2d1c8491abb21', 'Type': 'elbv2'}]}
2024-05-24 16:21:57,363 - INFO - Checking if Auto Scaling Group needs to be updated with new AMI or capacity settings
2024-05-24 16:21:57,363 - INFO - Retrieving current AMI ID from launch template lt-0a408c46add7b1b37 version 6
2024-05-24 16:21:57,893 - INFO - Updating Auto Scaling Group with new settings
2024-05-24 16:21:57,893 - INFO - AMI ID has changed, creating new version of the launch template
2024-05-24 16:21:58,941 - INFO - ASG updated to use new launch template version: 7
2024-05-24 16:21:58,941 - INFO - Starting instance refresh for ASG: zdt-app-asg-blue
2024-05-24 16:21:59,206 - INFO - Started instance refresh with ID: c59ace4d-342a-4dbb-b171-baedfa01f0c7
2024-05-24 16:21:59,206 - INFO - Waiting for instance refresh c59ace4d-342a-4dbb-b171-baedfa01f0c7 to complete for ASG: zdt-app-asg-blue
2024-05-24 16:21:59,370 - INFO - Instance refresh in progress...
2024-05-24 16:22:09,938 - INFO - Instance refresh in progress...
2024-05-24 16:22:20,501 - INFO - Instance refresh in progress...
2024-05-24 16:22:31,126 - INFO - Instance refresh in progress...
[...]
2024-05-24 16:29:15,038 - INFO - Instance refresh in progress...
2024-05-24 16:29:25,689 - INFO - Instance refresh Successful
2024-05-24 16:29:25,690 - INFO - Instance refresh completed successfully
2024-05-24 16:29:25,690 - INFO - Verifying that old instances in ASG: zdt-app-asg-blue are terminated
2024-05-24 16:29:25,914 - INFO - Old instances have been terminated
2024-05-24 16:29:25,915 - INFO - Old instances have been terminated
2024-05-24 16:29:25,915 - INFO - Deployment process completed successfully
```

```
# this is a failed deployment
❯ python scripts/deploy_app.py

2024-05-24 17:24:46,679 - INFO - Starting deployment process
2024-05-24 17:24:46,679 - INFO - Initializing AWS SDK
2024-05-24 17:24:46,692 - INFO - Found credentials in environment variables.
2024-05-24 17:24:46,769 - INFO - Retrieving details for Auto Scaling Group: zdt-app-asg-blue
2024-05-24 17:24:47,442 - INFO - ASG Details: {'AutoScalingGroupName': 'zdt-app-asg-blue', 'AutoScalingGroupARN': 'arn:aws:autoscaling:us-east-1:197490477086:autoScalingGroup:5c98a12e-9aea-49a2-ae26-17bf20796724:autoScalingGroupName/zdt-app-asg-blue', 'LaunchTemplate': {'LaunchTemplateId': 'lt-0a408c46add7b1b37', 'LaunchTemplateName': 'zdt-app-template-blue-20240523210214573900000001', 'Version': '9'}, 'MinSize': 1, 'MaxSize': 4, 'DesiredCapacity': 3, 'DefaultCooldown': 300, 'AvailabilityZones': ['us-east-1a', 'us-east-1b'], 'LoadBalancerNames': [], 'TargetGroupARNs': ['arn:aws:elasticloadbalancing:us-east-1:197490477086:targetgroup/synthetic-tg/c4c386b4af8f61ce', 'arn:aws:elasticloadbalancing:us-east-1:197490477086:targetgroup/tf-20240523092733985500000006/8da2d1c8491abb21'], 'HealthCheckType': 'EC2', 'HealthCheckGracePeriod': 300, 'Instances': [{'InstanceId': 'i-02c9a0c11c038b5ad', 'InstanceType': 't2.micro', 'AvailabilityZone': 'us-east-1a', 'LifecycleState': 'InService', 'HealthStatus': 'Healthy', 'LaunchTemplate': {'LaunchTemplateId': 'lt-0a408c46add7b1b37', 'LaunchTemplateName': 'zdt-app-template-blue-20240523210214573900000001', 'Version': '9'}, 'ProtectedFromScaleIn': False}, {'InstanceId': 'i-02decc7f59977baff', 'InstanceType': 't2.micro', 'AvailabilityZone': 'us-east-1a', 'LifecycleState': 'Terminating', 'HealthStatus': 'Healthy', 'LaunchTemplate': {'LaunchTemplateId': 'lt-0a408c46add7b1b37', 'LaunchTemplateName': 'zdt-app-template-blue-20240523210214573900000001', 'Version': '7'}, 'ProtectedFromScaleIn': False}, {'InstanceId': 'i-04e3e0476fa65e577', 'InstanceType': 't2.micro', 'AvailabilityZone': 'us-east-1a', 'LifecycleState': 'InService', 'HealthStatus': 'Healthy', 'LaunchTemplate': {'LaunchTemplateId': 'lt-0a408c46add7b1b37', 'LaunchTemplateName': 'zdt-app-template-blue-20240523210214573900000001', 'Version': '9'}, 'ProtectedFromScaleIn': False}, {'InstanceId': 'i-08a22c25904cb49b0', 'InstanceType': 't2.micro', 'AvailabilityZone': 'us-east-1a', 'LifecycleState': 'Terminating', 'HealthStatus': 'Healthy', 'LaunchTemplate': {'LaunchTemplateId': 'lt-0a408c46add7b1b37', 'LaunchTemplateName': 'zdt-app-template-blue-20240523210214573900000001', 'Version': '8'}, 'ProtectedFromScaleIn': False}, {'InstanceId': 'i-08ce631ce2ca9b9d4', 'InstanceType': 't2.micro', 'AvailabilityZone': 'us-east-1b', 'LifecycleState': 'InService', 'HealthStatus': 'Healthy', 'LaunchTemplate': {'LaunchTemplateId': 'lt-0a408c46add7b1b37', 'LaunchTemplateName': 'zdt-app-template-blue-20240523210214573900000001', 'Version': '9'}, 'ProtectedFromScaleIn': False}, {'InstanceId': 'i-0ce7c85ee45c8738a', 'InstanceType': 't2.micro', 'AvailabilityZone': 'us-east-1a', 'LifecycleState': 'Terminating', 'HealthStatus': 'Healthy', 'LaunchTemplate': {'LaunchTemplateId': 'lt-0a408c46add7b1b37', 'LaunchTemplateName': 'zdt-app-template-blue-20240523210214573900000001', 'Version': '8'}, 'ProtectedFromScaleIn': False}, {'InstanceId': 'i-0e35d8c24d7861eba', 'InstanceType': 't2.micro', 'AvailabilityZone': 'us-east-1b', 'LifecycleState': 'Terminating', 'HealthStatus': 'Healthy', 'LaunchTemplate': {'LaunchTemplateId': 'lt-0a408c46add7b1b37', 'LaunchTemplateName': 'zdt-app-template-blue-20240523210214573900000001', 'Version': '8'}, 'ProtectedFromScaleIn': False}], 'CreatedTime': datetime.datetime(2024, 5, 23, 20, 57, 10, 732000, tzinfo=tzutc()), 'SuspendedProcesses': [], 'VPCZoneIdentifier': 'subnet-0cc43bbe46640850a,subnet-0c5a8ae1a6f0b1c16', 'EnabledMetrics': [], 'Tags': [{'ResourceId': 'zdt-app-asg-blue', 'ResourceType': 'auto-scaling-group', 'Key': 'Name', 'Value': 'zdt-app', 'PropagateAtLaunch': True}], 'TerminationPolicies': ['Default'], 'NewInstancesProtectedFromScaleIn': False, 'ServiceLinkedRoleARN': 'arn:aws:iam::197490477086:role/aws-service-role/autoscaling.amazonaws.com/AWSServiceRoleForAutoScaling', 'TrafficSources': [{'Identifier': 'arn:aws:elasticloadbalancing:us-east-1:197490477086:targetgroup/synthetic-tg/c4c386b4af8f61ce', 'Type': 'elbv2'}, {'Identifier': 'arn:aws:elasticloadbalancing:us-east-1:197490477086:targetgroup/tf-20240523092733985500000006/8da2d1c8491abb21', 'Type': 'elbv2'}]}
2024-05-24 17:24:47,443 - INFO - Checking if Auto Scaling Group needs to be updated with new AMI or capacity settings
2024-05-24 17:24:47,443 - INFO - Retrieving current AMI ID from launch template lt-0a408c46add7b1b37 version 9
2024-05-24 17:24:48,006 - INFO - Updating Auto Scaling Group with new settings
2024-05-24 17:24:48,006 - INFO - AMI ID has changed, creating new version of the launch template
2024-05-24 17:24:49,030 - INFO - ASG updated to use new launch template version: 10
2024-05-24 17:24:49,030 - INFO - Starting instance refresh for ASG: zdt-app-asg-blue
2024-05-24 17:24:49,496 - ERROR - ClientError starting instance refresh: An error occurred (InstanceRefreshInProgress) when calling the StartInstanceRefresh operation: An Instance Refresh is already in progress and blocks the execution of this Instance Refresh.
2024-05-24 17:24:51,501 - INFO - Starting instance refresh for ASG: zdt-app-asg-blue
2024-05-24 17:24:52,199 - ERROR - ClientError starting instance refresh: An error occurred (InstanceRefreshInProgress) when calling the StartInstanceRefresh operation: An Instance Refresh is already in progress and blocks the execution of this Instance Refresh.
2024-05-24 17:24:54,203 - INFO - Starting instance refresh for ASG: zdt-app-asg-blue
2024-05-24 17:24:55,026 - ERROR - ClientError starting instance refresh: An error occurred (InstanceRefreshInProgress) when calling the StartInstanceRefresh operation: An Instance Refresh is already in progress and blocks the execution of this Instance Refresh.
2024-05-24 17:24:55,026 - ERROR - Error updating ASG with new AMI: RetryError[<Future at 0x103c60050 state=finished raised InstanceRefreshInProgressFault>]
```