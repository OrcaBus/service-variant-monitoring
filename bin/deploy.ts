#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { StatelessStack } from '../infrastructure/toolchain/stateless-stack';
import { StatefulStack } from '../infrastructure/toolchain/stateful-stack';
import { TOOLCHAIN_ENVIRONMENT } from '@orcabus/platform-cdk-constructs/deployment-stack-pipeline';
import { VariantMonitoringStack } from '../infrastructure/stage/deployment-stack';
import { getStackProps } from '../infrastructure/stage/config';
const app = new cdk.App();

const deployMode = app.node.tryGetContext('deployMode');
if (!deployMode) {
  throw new Error("deployMode is required in context (e.g. '-c deployMode=stateless')");
}

if (deployMode === 'stateless') {
  new StatelessStack(app, 'OrcaBusStatelessVariantMonitoringStack', {
    env: TOOLCHAIN_ENVIRONMENT,
  });
} else if (deployMode === 'stateful') {
  new StatefulStack(app, 'OrcaBusStatefulVariantMonitoringStack', {
    env: TOOLCHAIN_ENVIRONMENT,
  });
} else if (deployMode === 'direct') {
  // Direct deploy to the current account/region — useful for dev testing without the pipeline.
  // Usage: AWS_PROFILE=umccr-dev-pu pnpm cdk -c deployMode=direct deploy VariantMonitoringStack
  new VariantMonitoringStack(app, 'VariantMonitoringStack', {
    ...getStackProps('BETA'),
  });
} else {
  throw new Error("Invalid 'deployMode` set in the context");
}
