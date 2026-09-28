// Shared helpers for pages that post JSON to `/api/` and render ninja's error shape.

function extractErrorMessage(data, fallback) {
  const detail = data.detail;
  if (Array.isArray(detail)) {
    return detail.map(function(e){ return (e.ctx && e.ctx.error) || e.msg; }).join(' ') || fallback;
  }
  return detail || data.message || fallback;
}
