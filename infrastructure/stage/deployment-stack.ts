import * as path from 'path';
import { Construct } from 'constructs';
import { Architecture } from 'aws-cdk-lib/aws-lambda';
import { EventBus, IEventBus, Rule } from 'aws-cdk-lib/aws-events';
import { LambdaFunction } from 'aws-cdk-lib/aws-events-targets';
import { aws_lambda, Duration, Stack, StackProps } from 'aws-cdk-lib';
import { PythonFunction, PythonLayerVersion } from '@aws-cdk/aws-lambda-python-alpha';
import { ManagedPolicy, PolicyStatement, Role, ServicePrincipal } from 'aws-cdk-lib/aws-iam';
import {
  APP_ROOT,
  INCOMING_DETAIL_TYPE,
  INCOMING_EVENT_SOURCE,
  INCOMING_STATUS_FILTER,
  INCOMING_WORKFLOW_NAME,
} from './constants';

export interface VariantMonitoringStackProps extends StackProps {
  mainBusName: string;
  stage: string;
}

export class VariantMonitoringStack extends Stack {
  private readonly lambdaRuntimePythonVersion: aws_lambda.Runtime = aws_lambda.Runtime.PYTHON_3_12;
  private readonly mainBus: IEventBus;
  private readonly lambdaRole: Role;
  private readonly baseLayer: PythonLayerVersion;

  constructor(scope: Construct, id: string, props: VariantMonitoringStackProps) {
    super(scope, id, props);

    this.mainBus = EventBus.fromEventBusName(this, 'OrcaBusMain', props.mainBusName);

    // Shared Lambda execution role
    this.lambdaRole = new Role(this, 'LambdaRole', {
      assumedBy: new ServicePrincipal('lambda.amazonaws.com'),
      description: 'Lambda execution role for VariantMonitoring service',
    });
    this.lambdaRole.addManagedPolicy(
      ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')
    );

    // Allow the Lambda to emit events onto the bus
    this.lambdaRole.addToPolicy(
      new PolicyStatement({
        actions: ['events:PutEvents'],
        resources: [this.mainBus.eventBusArn],
      })
    );

    // Allow read access to any ICAv2 DRAGEN cache bucket (bucket name comes from outputUri at runtime)
    this.lambdaRole.addToPolicy(
      new PolicyStatement({
        actions: ['s3:GetObject', 's3:ListBucket'],
        resources: [
          'arn:aws:s3:::pipeline-*-cache-*',
          'arn:aws:s3:::pipeline-*-cache-*/*',
        ],
      })
    );

    // Allow reading the OrcaBus JWT token from Secrets Manager
    this.lambdaRole.addToPolicy(
      new PolicyStatement({
        actions: ['secretsmanager:GetSecretValue'],
        resources: [`arn:aws:secretsmanager:*:*:secret:orcabus/token-service-jwt*`],
      })
    );

    // Allow reading the hostname SSM parameter
    this.lambdaRole.addToPolicy(
      new PolicyStatement({
        actions: ['ssm:GetParameter'],
        resources: [`arn:aws:ssm:*:*:parameter/hosted_zone/umccr/name`],
      })
    );

    // Layer bundles Python dependencies from requirements.txt
    this.baseLayer = new PythonLayerVersion(this, 'BaseLayer', {
      entry: path.join(APP_ROOT),
      compatibleRuntimes: [this.lambdaRuntimePythonVersion],
      compatibleArchitectures: [Architecture.ARM_64],
      description: 'VariantMonitoring service dependencies (pydantic, pysam)',
    });

    this.createExtractVariantAfFunction(props);
  }

  private createExtractVariantAfFunction(props: VariantMonitoringStackProps): void {
    const extractFn = new PythonFunction(this, 'ExtractVariantAfFunction', {
      entry: path.join(APP_ROOT),
      runtime: this.lambdaRuntimePythonVersion,
      architecture: Architecture.ARM_64,
      index: 'variant_monitoring/lambdas/extract_variant_af.py',
      handler: 'lambda_handler',
      // pysam download + tabix queries need extra memory and time
      timeout: Duration.minutes(5),
      memorySize: 1024,
      ephemeralStorageSize: aws_lambda.Size.gibibytes(2),
      layers: [this.baseLayer],
      role: this.lambdaRole,
      environment: {
        EVENT_BUS_NAME: props.mainBusName,
        ORCABUS_TOKEN_SECRET_ID: 'orcabus/token-service-jwt',
        HOSTNAME_SSM_PARAMETER_NAME: '/hosted_zone/umccr/name',
      },
    });

    // EventBridge rule: route SUCCEEDED WorkflowRunStateChange events for dragen-wgts-dna
    const rule = new Rule(this, 'WorkflowRunStateChangeRule', {
      eventBus: this.mainBus,
      eventPattern: {
        source: [INCOMING_EVENT_SOURCE],
        detailType: [INCOMING_DETAIL_TYPE],
        detail: {
          workflow: { name: [INCOMING_WORKFLOW_NAME] },
          status: [INCOMING_STATUS_FILTER],
        },
      },
    });

    rule.addTarget(new LambdaFunction(extractFn));
  }
}
