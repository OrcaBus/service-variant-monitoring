import { StageName } from '@orcabus/platform-cdk-constructs/shared-config/accounts';
import { VariantMonitoringStackProps } from './deployment-stack';
import { EVENT_BUS } from './constants';

export const getStackProps = (stage: StageName): VariantMonitoringStackProps => {
  return {
    mainBusName: EVENT_BUS,
    stage: stage,
  };
};
