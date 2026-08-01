// pdfjs-dist 6.x 调用 ES 提案方法 Map.prototype.getOrInsertComputed，
// 项目无 core-js，浏览器与 Worker 线程均不原生支持，这里补齐。
if (typeof Map.prototype.getOrInsertComputed !== 'function') {
  Object.defineProperty(Map.prototype, 'getOrInsertComputed', {
    value: function getOrInsertComputed(key, computer) {
      if (this.has(key)) return this.get(key);
      const value = computer(key);
      this.set(key, value);
      return value;
    },
    writable: true,
    configurable: true,
    enumerable: false,
  });
}