// Форматирование чисел, денег и дат (перенос из прежнего vanilla app.js).
export const number = (value) => new Intl.NumberFormat('ru-RU').format(Number(value) || 0);

export const money = (value) => new Intl.NumberFormat('ru-RU', {
  style: 'currency', currency: 'RUB', maximumFractionDigits: 2,
}).format(Number(value) || 0);

export function date(value) {
  if (!value) return '—';
  const parsed = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

// Сколько аккаунтов можно купить на баланс (жадно, по возрастанию цены).
export function maxAffordable(prices, balance) {
  if (balance == null) return 0;
  let running = 0, count = 0;
  for (const price of [...prices].sort((a, b) => a - b)) {
    if (running + price > balance) break;
    running += price; count += 1;
  }
  return count;
}
