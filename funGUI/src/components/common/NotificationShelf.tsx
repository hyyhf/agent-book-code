import { useEffect, useState } from 'react';
import { notifyEventName, type NotifyEvent } from '../../utils/notify';

interface Notice extends NotifyEvent {
  id: number;
}

export function NotificationShelf() {
  const [notices, setNotices] = useState<Notice[]>([]);

  useEffect(() => {
    const handle = (event: Event) => {
      const detail = (event as CustomEvent<NotifyEvent>).detail;
      if (!detail?.message) return;
      const id = Date.now() + Math.random();
      setNotices((prev) => [...prev.slice(-3), { id, ...detail }]);
      window.setTimeout(() => {
        setNotices((prev) => prev.filter((notice) => notice.id !== id));
      }, 4200);
    };

    window.addEventListener(notifyEventName, handle);
    return () => window.removeEventListener(notifyEventName, handle);
  }, []);

  if (notices.length === 0) return null;

  return (
    <div className="notification-shelf" role="status" aria-live="polite">
      {notices.map((notice) => (
        <div className={`notification notification-${notice.kind}`} key={notice.id}>
          {notice.message}
        </div>
      ))}
    </div>
  );
}
