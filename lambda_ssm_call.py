import boto3
import json
import logging
import time
import os

from json import dumps
from httplib2 import Http

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
ssm_client = boto3.client("ssm")

LIFECYCLE_KEY = "LifecycleHookName"
ASG_KEY = "AutoScalingGroupName"
EC2_KEY = "EC2InstanceId"
RESPONSE_DOCUMENT_KEY = "DocumentIdentifiers"


S3BUCKET = os.environ['S3BUCKET']
SNSTARGET = os.environ['SNSTARGET']
DOCUMENT_NAME = os.environ['SSM_DOCUMENT_NAME']
ASGNAME=''

from datetime import datetime
DATE_PROCESS=datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f')[:-3]
print("DATE_PROCESS" + DATE_PROCESS)

class Result:
  def __init__(self, m, p):
    self.text = m
    self.branch = p

class Instance:
  def __init__(self, a, m, p, file, path):
    self.id = a
    self.name = m
    self.ip = p
    self.file = file
    self.path = path

########################  Modify the autosclaing group name and which you would like to take backup ###########
def backup_dir(ASGNAME):
    auto_scaling_group = ASGNAME
    print(auto_scaling_group)
    if auto_scaling_group == 'specific-asg':
        return "/var/log/"
    else:
        return "/var/log/"
#############################################
def check_response(response_json):
    try:
        if response_json['ResponseMetadata']['HTTPStatusCode'] == 200:
            return True
        else:
            return False
    except KeyError:
        return False

def list_document():
    document_filter_parameters = {'key': 'Name', 'value': DOCUMENT_NAME}
    response = ssm_client.list_documents(
        DocumentFilterList=[ document_filter_parameters ]
    )
    return response

def check_document():
    # If the document already exists, it will not create it.
    try:
        response = list_document()
        if check_response(response):
            logger.info("Documents list: %s", response)
            if response[RESPONSE_DOCUMENT_KEY]:
                logger.info("Documents exists: %s", response)
                return True
            else:
                return False
        else:
            logger.error("Documents' list error: %s", response)
            return False
    except Exception as e:
        logger.error("Document error: %s", str(e))
        return None   

def send_command(instance_id,LIFECYCLEHOOKNAME,ASGNAME, instance):
    # Until the document is not ready, waits in accordance to a backoff mechanism.
    while True:
        timewait = 1
        response = list_document()
        if any(response[RESPONSE_DOCUMENT_KEY]):
            break
        time.sleep(timewait)
        timewait += timewait
    try:
        BACKUPDIRECTORY= backup_dir(ASGNAME)
        print("send_command- Path:" + instance.path)
        print("send_command- File:" + instance.file)
        response = ssm_client.send_command(
            InstanceIds = [ instance_id ], 
            DocumentName=DOCUMENT_NAME,
            Parameters= {
            'ASGNAME' : [ASGNAME],
            'LIFECYCLEHOOKNAME' : [LIFECYCLEHOOKNAME],
            'BACKUPDIRECTORY' : [BACKUPDIRECTORY],
            'S3BUCKET' : [S3BUCKET],
            'SNSTARGET' : [SNSTARGET],
            'FILE' : [instance.file],
            'PATH' : [instance.path]
            },
            TimeoutSeconds= 600
            )
        if check_response(response):
            logger.info("Command sent: %s", response)
            print(response['Command']['CommandId'])
            return response['Command']['CommandId']
        else:
            logger.error("Command could not be sent: %s", response)
            return None
    except Exception as e:
        logger.error("Command could not be sent: %s", str(e))
        return None

def check_command(command_id, instance_id):
    timewait = 1
    while True:
        time.sleep(10)
        response_iterator = ssm_client.list_command_invocations(
            CommandId = command_id, 
            InstanceId = instance_id, 
            Details=False
            )
        logging.info( "list command invoations: %s", response_iterator)
            
        if check_response(response_iterator):
            response_iterator_status = response_iterator['CommandInvocations'][0]['Status']
            if response_iterator_status != 'Pending':
                if response_iterator_status == 'InProgress' or response_iterator_status == 'Success':
                    logging.info( "Status: %s", response_iterator_status)
                    return True
                else:
                    logging.error("ERROR: status: %s", response_iterator)
                    return False
        time.sleep(timewait)
        timewait += timewait

def abandon_lifecycle(life_cycle_hook, auto_scaling_group, instance_id):
    asg_client = boto3.client('autoscaling')
    try:
        response = asg_client.complete_lifecycle_action(
            LifecycleHookName=life_cycle_hook,
            AutoScalingGroupName=auto_scaling_group,
            LifecycleActionResult='ABANDON',
            InstanceId=instance_id
            )
        if check_response(response):
            logger.info("Lifecycle hook abandoned correctly: %s", response)
        else:
            logger.error("Lifecycle hook could not be abandoned: %s", response)
    except Exception as e:
        logger.error("Lifecycle hook abandon could not be executed: %s", str(e))
        return None    

def get_instance_info(instance_id):
    client = boto3.client('ec2')
    name=''
    ip=''
    file=""
    path=""
    # aws s3 cp /tmp/${INSTANCEID}-${INSTANCEIP}.tar s3://{{S3BUCKET}}/{{ASGNAME}}/${INSTANCEID}_${INSTANCEIP}_{{DATEPROCESS}}/
    
    try:
        response = client.describe_instances(
            InstanceIds=[
                instance_id,
            ],
            )

        for r in response['Reservations']:
            for i in r['Instances']:
                print (i['PrivateIpAddress'])
                ip=i['PrivateIpAddress']
                for t in i['Tags']:
                    if t['Key'] == 'Name':
                        name=t['Value']
                        print (t['Value'])    

        if check_response(response):
            logger.info("Get Instance Info with success: %s", response)
            file=instance_id + "-" + ip + "_" + DATE_PROCESS + ".tar"
            path="s3://" + S3BUCKET + "/" + name + "/" 
            # path="s3://" + S3BUCKET + "/" + name + "/" + instance_id + "_" + ip + "_" + DATE_PROCESS + "/"
            instance = Instance(instance_id, name, ip, file, path)
            print("Path:" + path)
            print("file:" + file)
            return instance
        else:
            logger.error("Could NOT Get Instance Info: %s", response)
    except Exception as e:
        logger.error("Could NOT Get Instance Info: %s", str(e))
        return None    

def lambda_handler(event, context):
    try:
        logger.info(json.dumps(event))
        message = event['detail']
        print (message)
        if LIFECYCLE_KEY in message and ASG_KEY in message:
            life_cycle_hook = message[LIFECYCLE_KEY]
            print (life_cycle_hook)
            auto_scaling_group = message[ASG_KEY]
            ASGNAME=auto_scaling_group
            print (auto_scaling_group)
            instance_id = message[EC2_KEY]
            if check_document():
                instance = get_instance_info(instance_id)
                command_id = send_command(instance_id,life_cycle_hook,auto_scaling_group, instance)
                print (command_id)
                if command_id != None:
                    if check_command(command_id, instance_id):
                        logging.info("Lambda executed correctly")
                    else:
                        abandon_lifecycle(life_cycle_hook, auto_scaling_group, instance_id)
                else:
                    abandon_lifecycle(life_cycle_hook, auto_scaling_group, instance_id)
            else:
                abandon_lifecycle(life_cycle_hook, auto_scaling_group, instance_id)
        else:
            logging.error("No valid JSON message: %s", parsed_message)
    except Exception as e:
        logging.error("Error: %s", str(e))