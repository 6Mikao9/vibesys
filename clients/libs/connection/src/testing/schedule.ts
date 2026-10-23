/** Held-reply scheduling shared by host-generation sequences and cross-process seam simulations. */
import {Seeded} from '@vibesys/store/testing';

/** A request stays owned by its caller even when a reply is delivered after cancellation. */
export class HeldReply<T> {
  readonly reply: Promise<T>;
  answered = false;
  readonly #resolve: (value: T) => void;
  readonly #reject: (error: unknown) => void;

  constructor(readonly signal: AbortSignal) {
    let resolve = (_value: T): void => {};
    let reject = (_error: unknown): void => {};
    this.reply = new Promise<T>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    this.#resolve = resolve;
    this.#reject = reject;
  }

  answer(value: T): void {
    this.answered = true;
    this.#resolve(value);
  }
  fail(error: unknown): void {
    this.answered = true;
    this.#reject(error);
  }
}

interface Delivery {
  readonly lane: object;
  readonly run: () => void;
}

/**
 * FIFO in each lane, seeded ordering between lanes. Progress waits on owned operations or new
 * deliveries, never on a guessed number of microtask turns. A bound counts delivered events.
 */
export class Schedule {
  readonly random: Seeded;
  readonly #queue: Delivery[] = [];
  #wake: (() => void) | undefined;
  #pending = 0;
  events = 0;

  constructor(readonly seed: number) {
    this.random = new Seeded(seed);
  }

  get pending(): number {
    return this.#pending;
  }
  get queued(): number {
    return this.#queue.length;
  }

  post(lane: object, run: () => void): void {
    this.#queue.push({lane, run});
    this.#wake?.();
  }

  track<T>(operation: Promise<T>): Promise<T> {
    this.#pending += 1;
    return operation.finally(() => {
      this.#pending -= 1;
      this.#wake?.();
    });
  }

  async run<T>(operation: Promise<T>, bound = 512): Promise<T> {
    let settled = false;
    const result = this.track(operation).finally(() => {
      settled = true;
      this.#wake?.();
    });
    // Own the rejection immediately, while deliveries are still in flight.
    void result.catch(() => {});
    const start = this.events;
    while (!settled || this.#queue.length > 0) {
      if (this.events - start >= bound)
        throw new Error(`seed ${this.seed}: exceeded ${bound} scheduler events`);
      if (this.#queue.length === 0) {
        await new Promise<void>(resolve => {
          this.#wake = resolve;
        });
        this.#wake = undefined;
      } else {
        const lanes = [...new Set(this.#queue.map(delivery => delivery.lane))];
        const lane = this.random.pick(lanes);
        const index = this.#queue.findIndex(delivery => delivery.lane === lane);
        const [delivery] = this.#queue.splice(index, 1);
        this.events += 1;
        delivery?.run();
        // A delivery may start a new owned operation before the next delivery is chosen.
        await Promise.resolve();
      }
    }
    return result;
  }
}
