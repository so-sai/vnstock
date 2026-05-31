export function formatPercent(value: number): string {
  if (Math.abs(value) >= 100) {
    return `${value >= 0 ? '+' : ''}${Math.round(value).toLocaleString('vi-VN')}%`;
  }
  if (Math.abs(value) >= 10) {
    return `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`;
  }
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

export function formatRatio(value: number): string {
  if (value <= 0) return '0.00';
  return value.toFixed(2);
}

export function formatPrice(price: number): string {
  return price.toLocaleString('vi-VN');
}

export function formatVND(amount: number): string {
  if (amount >= 1_000_000_000) return `${(amount / 1_000_000_000).toFixed(2)} tỷ`;
  if (amount >= 1_000_000) return `${(amount / 1_000_000).toFixed(2)} tr`;
  return `${amount.toLocaleString('vi-VN')}`;
}
