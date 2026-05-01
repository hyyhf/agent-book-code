export type NotifyKind = 'success' | 'error' | 'warning' | 'info';

export interface NotifyEvent {
  kind: NotifyKind;
  message: string;
}

const EVENT_NAME = 'fungui-notify';

function emit(kind: NotifyKind, message: string) {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<NotifyEvent>(EVENT_NAME, { detail: { kind, message } }));
}

export const notify = {
  success: (message: string) => emit('success', message),
  error: (message: string) => emit('error', message),
  warning: (message: string) => emit('warning', message),
  info: (message: string) => emit('info', message),
};

export { EVENT_NAME as notifyEventName };
