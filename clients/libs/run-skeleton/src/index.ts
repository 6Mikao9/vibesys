/** Additive store.v1 read path. No desktop or legacy run-session coupling. */

export type {SkeletonCache} from './cache-role.js';
export {type RunTransport, skeletonLoaderMachineFor, startSkeletonLoader} from './loader.js';
export {
  MAX_ATTEMPTS,
  RETRY_CAP_MS,
  retryDelay,
  type SkeletonLoaderInput,
  type SkeletonLoadFailure,
  skeletonLoaderMachine,
} from './loader-machine.js';
export {
  epochHasEnded,
  ingestSkeleton,
  ingestTail,
  type LoadProgress,
  loadProgress,
  observeRun,
  progressFraction,
  type RunLivenessHint,
  type SkeletonLoad,
} from './loading.js';
export {foldSkeleton, type Skeleton, seedSkeleton, seekSkeleton} from './model.js';
export {type LoadRefusal, refusalOf} from './status.js';
