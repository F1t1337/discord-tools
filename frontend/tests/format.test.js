import { describe, it, expect } from 'vitest';
import { number, money, date, maxAffordable } from '../src/lib/format.js';

describe('format', () => {
  it('number formats with ru grouping and coerces junk to 0', () => {
    expect(number(1234567).replace(/\s/g, '')).toBe('1234567'); // ru uses non-breaking spaces
    expect(number(null)).toBe('0');
    expect(number('abc')).toBe('0');
  });

  it('money renders RUB and coerces junk to zero', () => {
    expect(money(0)).toContain('0');
    expect(money(null)).toContain('₽');
  });

  it('date returns em dash for empty and invalid', () => {
    expect(date(null)).toBe('—');
    expect(date('not-a-date')).toBe('—');
    expect(date(0)).toBe('—');
  });

  it('date accepts unix seconds', () => {
    expect(date(1_700_000_000)).not.toBe('—');
  });
});

describe('maxAffordable', () => {
  it('greedily counts cheapest items within balance', () => {
    expect(maxAffordable([10, 20, 30, 40], 55)).toBe(2); // 10+20<=55, +30>55
    expect(maxAffordable([10, 20, 30], 60)).toBe(3);
    expect(maxAffordable([10, 20], 5)).toBe(0);
  });

  it('returns 0 when balance is unknown', () => {
    expect(maxAffordable([10, 20], null)).toBe(0);
  });

  it('does not mutate the input order', () => {
    const prices = [30, 10, 20];
    maxAffordable(prices, 100);
    expect(prices).toEqual([30, 10, 20]);
  });
});
