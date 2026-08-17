import React, { useState, useCallback, useRef } from 'react';

let toastId = 0;
let addToastFn = null;

export function showToast(msg, type = '') {
    if (addToastFn) addToastFn(msg, type);
}

export default function Toast() {
    const [toasts, setToasts] = useState([]);
    const timersRef = useRef({});

    addToastFn = useCallback((msg, type) => {
        const id = ++toastId;
        setToasts(prev => [...prev, { id, msg, type }]);
        timersRef.current[id] = setTimeout(() => {
            setToasts(prev => prev.filter(t => t.id !== id));
            delete timersRef.current[id];
        }, 3200);
    }, []);

    return (
        <div className="toast-container">
            {toasts.map(t => (
                <div key={t.id} className={`toast ${t.type}`}>{t.msg}</div>
            ))}
        </div>
    );
}
