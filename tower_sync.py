#!/usr/bin/python

import json
import time
import botocore.session
from boto import ec2
import boto.sqs
import logging
import os
import subprocess
LOG = logging.getLogger(__name__)

CREATE_TOWER_GROUPS = True
SQS_QUEUE_NAME = os.environ['AWS_SQS_QUEUE_NAME'] if os.environ['AWS_SQS_QUEUE_NAME'] else "aws_asg"
AWS_REGION = os.environ['AWS_REGION'] if os.environ['AWS_REGION'] else 'us-east-1'
DEFAULT_INVENTORY = 1
ORGANIZATION = os.environ['ORGANIZATION'] if os.environ['ORGANIZATION'] else 1
TOWER_USER_NAME = os.environ['TOWER_USER_NAME'] if os.environ['TOWER_USER_NAME'] else "admin"
TOWER_PASSWORD = os.environ['TOWER_PASSWORD'] if os.environ['TOWER_PASSWORD'] else "password"
TOWER_HOST = os.environ['TOWER_HOST'] if os.environ['TOWER_HOST'] else "http://0.0.0.0"
TOWER_VERIFY_SSL = os.environ['TOWER_VERIFY_SSL'] if os.environ['TOWER_VERIFY_SSL'] else False
# use the undocumented API client from the tower-cli tool


def write_configs():
    try:
        with open('/etc/tower/tower_cli.cfg', 'w') as fd:
            fd.write("[general]\n")
            fd.write("username = %s\n" % TOWER_USER_NAME)
            fd.write("password = %s \n" % TOWER_PASSWORD)
            fd.write("host = %s\n" % TOWER_HOST)
            fd.write("verify_ssl = %s\n" % TOWER_VERIFY_SSL)
        fd.close()
        subprocess.call("tower-cli config --scope=global", shell=True)
    except:
        pass

write_configs()

import tower_cli

group_resource = tower_cli.get_resource('group')
print group_resource.list(all_pages=True)['results']
host_resource = tower_cli.get_resource('host')
job_resource = tower_cli.get_resource('job')
job_template_resource = tower_cli.get_resource('job_template')
inventory_resource = tower_cli.get_resource('inventory')

# set up our AWS endpoints
ec2_conn = ec2.connect_to_region(AWS_REGION)
sqs_conn = boto.sqs.connect_to_region(AWS_REGION)

# we use botocore instead of boto for the newest ASG feature, as most of the
# SDKs lag botocore.
bc_session = botocore.session.get_session()
bc_asg = bc_session.create_client('autoscaling', region_name=AWS_REGION)


def _get_instance(instance_id):
    reservations = ec2_conn.get_all_instances(instance_ids=[instance_id])
    if reservations:
        return reservations[0].instances[0]
    else:
        LOG.error("Instance does not exist")
        raise Exception("Instance does not exist")


def _get_tower_group(group_name, create=True, inventory_name=DEFAULT_INVENTORY):
    """
    Given a group name, find or optionally create a corresponding Tower group.
    This is used to pair an AWS autoscaling group to a Tower inventory group.
    """

    groups = group_resource.list(all_pages=True)['results']
    matching_groups = [g for g in groups if g['name'] == group_name]
    if not matching_groups:
        # no matching group
        if create:
            tower_group = group_resource.create(name=group_name,
                    inventory=_get_inventory_id(inventory_name, create=True),
                    description="auto created ASG group")
        else:
            LOG.error("No Matching group Found")
            print "No Matching group Found"
            raise Exception("no matching group")
    else:
        tower_group = matching_groups[0]
    return tower_group


def get_tower_host(host_name_or_ip, inventory=DEFAULT_INVENTORY):
    hosts = host_resource.list(inventory=inventory, all_pages=True)['results']
    matching_hosts = [h for h in hosts if h['instance_id'] == host_name_or_ip]
    if matching_hosts:
        return matching_hosts[0]
    return None


def _get_inventory_id(inventory_name, create=False):
    inventories = inventory_resource.list(all_pages=True)['results']
    matching_inventory = [i for i in inventories if i['name'] == inventory_name]
    if not matching_inventory:
        if create:
            LOG.info("Creating new Inventory %s" % inventory_name)
            inventory = inventory_resource.create(name=inventory_name,
                                                  description="auto created inventory group",
                                                  organization=ORGANIZATION)
            inventory_id = inventory['id']
        else:
            inventory_id = DEFAULT_INVENTORY
    else:
        inventory_id = matching_inventory[0]['id']
    return inventory_id


def _launch_tower_job(instance_environment, instance_role, asg_name):
    LOG.info("launching job for %s environment with service %s" % (instance_environment, instance_role))
    extra_vars = ["env=%s" % instance_environment, "target=%s" % asg_name, "service_deployed=all_services"]
    inventory_name = "%s-%s" % (instance_environment, instance_role)
    job_resource.launch(job_template=instance_role, extra_vars=extra_vars, inventory=_get_inventory_id(inventory_name))
    LOG.info("Successfully started job for %s" % inventory_name)


def _add_instance_to_inventory(msg):
    instance = _get_instance(msg['EC2InstanceId'])
    tower_group = _get_tower_group(msg['AutoScalingGroupName'],
                                create=CREATE_TOWER_GROUPS,
                                inventory_name=_get_inventory_name_from_instance(instance))

    LOG.info("Adding instance %s to inventory group %s" % (str(msg['EC2InstanceId']), str(msg['AutoScalingGroupName'])))
    new_host = host_resource.create(
                    name=instance.private_ip_address,
                    description=instance.tags.get('Name', '<no name>'),
                    instance_id=msg['EC2InstanceId'],
                    inventory=tower_group['inventory']
                    )

    host_resource.associate(new_host['id'], tower_group['id'])
    # wait for 120 seconds to instance to come up
    time.sleep(240)
    instance_environment = instance.tags.get('Environment', 'None')
    instance_role = instance.tags.get('Role', 'None')
    # launch job
    _launch_tower_job(instance_environment, instance_role, msg['AutoScalingGroupName'])
    LOG.info("instance  %s configured successfully" % str(msg['EC2InstanceId']))


def _get_inventory_name_from_instance(instance):
    if instance:
        return "%s-%s" %(instance.tags.get('Environment'), instance.tags.get('Role'))


def _remove_instance_from_inventory(msg):
    try:
        # get group
        LOG.info("Removing instance  %s from inventory response" % str(msg['EC2InstanceId']))
        instance = _get_instance(msg['EC2InstanceId'])
        inventory_name = _get_inventory_name_from_instance(instance)
        tower_group = _get_tower_group(msg['AutoScalingGroupName'],
                                      create=CREATE_TOWER_GROUPS, inventory_name=inventory_name)
        host = get_tower_host(msg['EC2InstanceId'], _get_inventory_id(inventory_name))
        if host:
            host_resource.disassociate(host['id'], tower_group['id'])
            host_resource.delete(name=host['name'])
        LOG.info("instance  %s removed successfully from inventory response" % str(msg['EC2InstanceId']))
    except Exception as e:
        LOG.error("Failed to delete instance  %s from inventory response %s" % str(msg['EC2InstanceId']), e)


def _lifecycle_response(msg, cont=True):
    LOG.info("Generating Lifecycle response")
    result = "CONTINUE" if cont else "ABANDON"
    if msg['LifecycleHookName'] == "NewHost":
        bc_asg.complete_lifecycle_action(
                    LifecycleHookName=msg['LifecycleHookName'],
                    AutoScalingGroupName=msg['AutoScalingGroupName'],
                    LifecycleActionToken=msg['LifecycleActionToken'],
                    LifecycleActionResult=result)
    elif msg['LifecycleHookName'] == "RemoveHost":
        bc_asg.complete_lifecycle_action(
                    LifecycleHookName=msg['LifecycleHookName'],
                    AutoScalingGroupName=msg['AutoScalingGroupName'],
                    InstanceId=msg['EC2InstanceId'],
                    LifecycleActionResult=result)
    LOG.info("lifecycle response succeeded hook %s for asg %s"
             % (str(msg['LifecycleHookName']), str(msg['AutoScalingGroupName'])))



def main():
    msg_queue = sqs_conn.get_queue(SQS_QUEUE_NAME)
    while True:
        try:
            LOG.info("Fetching Queue message")
            m = msg_queue.read()
            if m:
                msg = json.loads(m.get_body())
                if "LifecycleHookName" not in msg:
                    # Ignore Messages without Lifecycle Hook Name
                    continue
                LOG.info("responding to lifecycle %s" % msg['LifecycleHookName'])
                print "responding to lifecycle %s" % msg['LifecycleHookName']
                try:
                    if msg['LifecycleHookName'] == "NewHost":
                        print("Processing New Host Hook")
                        _add_instance_to_inventory(msg)
                    elif msg['LifecycleHookName'] == "RemoveHost":
                        print("Processing RemoveHost Hook")
                        _remove_instance_from_inventory(msg)
                    msg_queue.delete_message(m)
                    _lifecycle_response(msg)
                except Exception as e:
                    LOG.error("Failed to Process Lifecycle notification %s", e)
                    # abort the lifecycle step
                    try:
                        _lifecycle_response(msg, cont=False)
                    except Exception as e:
                        print "Failed to Process retry Lifecycle notification %s skipping" % e
                        LOG.error("Failed to Process retry Lifecycle notification %s skipping", e)

            else:
                print "Pausing"
                LOG.info("No Message Found pausing for 10 Seconds")
                time.sleep(10)
        except Exception as e:
            LOG.error("Failed to Process Queue message %s" % e)
            print "Failed to Process Queue message %s" % e
            time.sleep(5)

if __name__ == "__main__":
    print "Starting Tower Sync"
    fh = logging.FileHandler("/root/tower_sync.app.log")
    fh.setLevel(logging.DEBUG)
    # create formatter and add it to the handlers
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    # add the handlers to logger
    LOG.addHandler(fh)
    main()
    print "Exiting Tower Sync"
