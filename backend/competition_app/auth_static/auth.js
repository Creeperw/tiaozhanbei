const form = document.querySelector('#auth-form');
const loginTab = document.querySelector('#login-tab');
const registerTab = document.querySelector('#register-tab');
const displayNameField = document.querySelector('#display-name-field');
const confirmField = document.querySelector('#confirm-field');
const password = document.querySelector('#password');
const confirmPassword = document.querySelector('#confirm-password');
const errorNode = document.querySelector('#auth-error');
const submitButton = document.querySelector('#submit-button');
let mode = 'login';

function safeNext() {
  const next = new URLSearchParams(window.location.search).get('next') || '/chat/';
  return next.startsWith('/') && !next.startsWith('//') ? next : '/chat/';
}

function setMode(nextMode) {
  mode = nextMode;
  const registering = mode === 'register';
  loginTab.setAttribute('aria-selected', String(!registering));
  registerTab.setAttribute('aria-selected', String(registering));
  displayNameField.hidden = !registering;
  confirmField.hidden = !registering;
  confirmPassword.required = registering;
  password.autocomplete = registering ? 'new-password' : 'current-password';
  document.querySelector('#form-eyebrow').textContent = registering ? 'CREATE YOUR SPACE' : 'WELCOME BACK';
  document.querySelector('#form-title').textContent = registering ? '建立个人学习档案' : '继续你的学习';
  document.querySelector('#form-intro').textContent = registering ? '注册后将直接进入你的独立学习空间。' : '使用已有账号进入个人学习空间。';
  submitButton.textContent = registering ? '注册并进入' : '登录并继续';
  errorNode.textContent = '';
}

loginTab.addEventListener('click', () => setMode('login'));
registerTab.addEventListener('click', () => setMode('register'));

form.addEventListener('submit', async event => {
  event.preventDefault();
  errorNode.textContent = '';
  if (!form.reportValidity()) return;
  if (mode === 'register' && password.value !== confirmPassword.value) {
    errorNode.textContent = '两次输入的密码不一致。';
    confirmPassword.focus();
    return;
  }
  submitButton.disabled = true;
  submitButton.textContent = mode === 'register' ? '正在建立档案…' : '正在登录…';
  const payload = {
    username: document.querySelector('#username').value.trim(),
    password: password.value,
  };
  if (mode === 'register') {
    const displayName = document.querySelector('#display-name').value.trim();
    if (displayName) payload.display_name = displayName;
  }
  try {
    const response = await fetch(`/api/v1/auth/${mode}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(body.detail)
        ? body.detail.map(item => item.msg).join('；')
        : body.detail;
      throw new Error(detail || '操作失败，请稍后重试。');
    }
    sessionStorage.setItem('competition.auth.user_id', body.user.user_id);
    sessionStorage.setItem('competition.auth.display_name', body.user.display_name);
    window.location.replace(safeNext());
  } catch (error) {
    errorNode.textContent = error.message || '连接失败，请稍后重试。';
    submitButton.disabled = false;
    submitButton.textContent = mode === 'register' ? '注册并进入' : '登录并继续';
  }
});
