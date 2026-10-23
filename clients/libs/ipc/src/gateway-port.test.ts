/** Same cases run over Electron's MessageChannelMain in desktop-main/e2e/gateway.spec.ts. */
import {expect, test} from 'bun:test';
import {readFileSync} from 'node:fs';
import {FakeGatewayPort} from './testing/index.js';

const cases: Array<{
  name: string;
  messages: number[][];
  receiverClosed: boolean;
  expected: number[][];
}> = JSON.parse(
  readFileSync(
    new URL('../../../../proto/testdata/gateway_port/contract.json', import.meta.url),
    'utf8',
  ),
);
for (const row of cases)
  test(`Fake gateway port contract: ${row.name}`, () => {
    const tasks: Array<() => void> = [];
    const {main, page} = FakeGatewayPort.pair((_lane, task) => tasks.push(task), structuredClone);
    const received: number[][] = [];
    let closed = 0;
    page.listen(value => received.push([...(value as Uint8Array)]));
    page.on('close', () => {
      closed += 1;
    });
    main.on('close', () => {
      closed += 1;
    });
    if (row.receiverClosed) page.close();
    for (const value of row.messages) {
      const bytes = new Uint8Array(value);
      main.postMessage(bytes);
      bytes.fill(99);
    }
    main.close();
    while (tasks.length) tasks.shift()?.();
    expect(received).toEqual(row.expected);
    expect(closed).toBe(row.receiverClosed ? 0 : 1);
  });
