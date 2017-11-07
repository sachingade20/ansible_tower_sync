# ansible_tower_sync

This is opensource project for PROVISIONING AN AUTOSCALING INFRASTRUCTURE USING ANSIBLE Tower AWX .

This script is polls aws SQS service and creates

1] Inventory in Ansible Tower with Name <instance-tag-environment>-<instance-tag-role> e.g Its mandatory to have Role and Environment tag to instance.

e.g 

            Role=mongodb 	
            Environment=staging 

Inventory for this instance will be created with staging-mongodb and host gets added with private IP adderess.

2] This script also triggers job with template name <role> so its important to have role template created in ansible tower.
e.g mongodb

This Script needs following environment is

      AWS_SQS_QUEUE_NAME: "aws-autoscaling-sqs"
      AWS_REGION: us-east-1
      ORGANIZATION: 1
      TOWER_USER_NAME: admin
      TOWER_PASSWORD: password
      TOWER_VERIFY_SSL: "False"
      TOWER_HOST: http://awxweb
      AWS_SECRET_ACCESS_KEY: "XXXXXX"
      AWS_ACCESS_KEY_ID: "XXXXXXX"


This is available with dockerhub with  docker pull sachinpgade/tower_sync
