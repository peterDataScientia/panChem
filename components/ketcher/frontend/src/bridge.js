// Streamlit custom component protocol. No timers or polling.
export function send(type, properties = {}) {
  window.parent.postMessage(
    { isStreamlitMessage: true, type, ...properties },
    '*'
  );
}

export function announceReady() {
  send('streamlit:componentReady', { apiVersion: 1 });
  send('streamlit:setFrameHeight', { height: 680 });
}

export function saveResult(value) {
  send('streamlit:setComponentValue', { value, dataType: 'json' });
}

export function onInput(callback) {
  const receiver = (event) => {
    if (event.source !== window.parent || event.data?.type !== 'streamlit:render') return;
    callback(event.data.args || {});
  };
  window.addEventListener('message', receiver);
  return () => window.removeEventListener('message', receiver);
}
