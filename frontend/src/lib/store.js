// Глобальные реактивные сторы приложения.
import { writable } from 'svelte/store';

export const VIEWS = ['overview', 'statistics', 'sellers', 'purchase', 'accounts', 'tokens', 'network', 'proxies', 'logs', 'settings'];

export function currentHash() {
  const hash = location.hash.slice(1);
  return VIEWS.includes(hash) ? hash : 'overview';
}

export const session = writable({ authenticated: false, username: null, booted: false, error: '' });
export const route = writable(currentHash());
export const liveState = writable({ mode: 'connecting', at: null }); // connecting|live|polling|offline
export const autoLive = writable(true);
export const refreshTick = writable(0);

export const NAV = [
  { section: 'Обзор', items: [
    { id: 'overview', label: 'Дашборд', icon: 'overview' },
    { id: 'statistics', label: 'Статистика', icon: 'wallet' },
    { id: 'sellers', label: 'Продавцы', icon: 'users' },
  ] },
  { section: 'Работа', items: [
    { id: 'purchase', label: 'Задача покупки', icon: 'purchase' },
    { id: 'accounts', label: 'Аккаунты', icon: 'accounts' },
    { id: 'tokens', label: 'Токены', icon: 'tokens' },
  ] },
  { section: 'Инфраструктура', items: [
    { id: 'proxies', label: 'Прокси', icon: 'proxies' },
    { id: 'network', label: 'Сеть и лимиты', icon: 'network' },
  ] },
  { section: 'Система', items: [
    { id: 'logs', label: 'Журнал', icon: 'logs' },
    { id: 'settings', label: 'Настройки', icon: 'settings' },
  ] },
];

export const TITLES = {
  statistics: ['Статистика', 'Расходы, доходы и прибыль по закупкам и сдачам.'],
  sellers: ['Продавцы', 'Рейтинг продавцов: у кого ниже цена за валидный аккаунт — тот лучше.'],
  overview: ['Дашборд', 'Текущее состояние системы и последние изменения.'],
  purchase: ['Задача покупки', 'Покупка аккаунтов с LZT по фильтрам и обработка в реальном времени.'],
  accounts: ['Аккаунты', 'Поиск и просмотр записей без раскрытия токенов доступа.'],
  tokens: ['Токены', 'Загрузка токенов в обработку и выгрузка готовых.'],
  network: ['Сеть и лимиты', 'Закрепление прокси и ответы Discord за последние 1 и 5 минут.'],
  proxies: ['Прокси', 'Загрузка, проверка на живость и пул рабочих прокси.'],
  logs: ['Журнал событий', 'События приложения и диагностика сервера.'],
  settings: ['Настройки', 'Параметры доступа и текущая конфигурация сервера.'],
};

export const STATUS_LABELS = {
  pending_review: 'Незавершённые', new: 'Новые', validated: 'Проверены', cleaning: 'В очистке',
  cleaned: 'Очищены', ready: 'Готовы', sent: 'Отправлены', invalid: 'Невалидные', locked: 'Заблокированы',
};

let toastTimer = null;
export const toastMessage = writable('');
export function toast(message) {
  toastMessage.set(message);
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastMessage.set(''), 4500);
}

export function tick() { refreshTick.update((n) => n + 1); }

// Состояние задачи покупки — общий стор, чтобы глобальный индикатор был виден на любой вкладке.
export const purchaseStatus = writable(null);

export const confirmState = writable(null); // {title, text, resolve}
export function confirmDialog(title, text) {
  return new Promise((resolve) => confirmState.set({ title, text, resolve }));
}
