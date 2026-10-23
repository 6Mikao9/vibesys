import type {SteerDeliveryFact} from '@vibesys/run-state';
import {useEffect, useState} from 'react';
import {steerDeliveries} from '../../state/index.js';
import {type SteerPorts, steerActor} from './steer.js';

/** The steer delivery steps the run's records report; empty until a record arrives. */
export function useSteerDeliveries(
  ports: SteerPorts,
  enabled: boolean,
): readonly SteerDeliveryFact[] {
  const [facts, setFacts] = useState<readonly SteerDeliveryFact[]>([]);
  useEffect(() => {
    if (!enabled) return;
    const actor = steerActor(ports);
    let known = actor.getSnapshot().context.fold;
    const subscription = actor.subscribe(snapshot => {
      if (snapshot.context.fold === known) return;
      known = snapshot.context.fold;
      setFacts(steerDeliveries(known));
    });
    actor.start();
    return () => {
      subscription.unsubscribe();
      actor.stop();
    };
  }, [ports, enabled]);
  return facts;
}
