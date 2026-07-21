(() => {
  const nativeFetch = window.fetch.bind(window);
  const redirectToLogin = () => {
    sessionStorage.removeItem('competition.auth.user_id');
    sessionStorage.removeItem('competition.auth.display_name');
    const next = `${window.location.pathname}${window.location.search}`;
    window.location.replace(`/auth/?next=${encodeURIComponent(next)}`);
  };

  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
    if (response.status === 401 && !url.includes('/api/v1/auth/')) redirectToLogin();
    return response;
  };

  window.competitionAuthReady = nativeFetch('/api/v1/auth/me')
    .then(async response => {
      if (!response.ok) throw new Error('unauthorized');
      const body = await response.json();
      const user = body.user;
      sessionStorage.setItem('competition.auth.user_id', user.user_id);
      sessionStorage.setItem('competition.auth.display_name', user.display_name);
      localStorage.removeItem('competition.chat.v1');
      localStorage.removeItem('competition_demo_langgraph_run_v1');
      window.currentAuthUser = user;
      window.dispatchEvent(new CustomEvent('competition:auth-ready', {detail: user}));
      const identity = document.querySelector('#auth-user');
      if (identity) {
        const name = identity.querySelector('[data-auth-name]');
        if (name) name.textContent = user.display_name;
        identity.hidden = false;
      }
      const learnerId = document.querySelector('#learner-id');
      if (learnerId) {
        learnerId.value = user.user_id;
        learnerId.readOnly = true;
        learnerId.title = '学习者身份由当前登录账号确定';
      }
      return user;
    })
    .catch(() => {
      redirectToLogin();
      return null;
    });

  document.addEventListener('click', async event => {
    const logout = event.target.closest('[data-auth-logout]');
    if (!logout) return;
    logout.disabled = true;
    try { await nativeFetch('/api/v1/auth/logout', {method:'POST'}); } finally {
      sessionStorage.removeItem('competition.auth.user_id');
      sessionStorage.removeItem('competition.auth.display_name');
      window.location.replace('/auth/');
    }
  });
})();
