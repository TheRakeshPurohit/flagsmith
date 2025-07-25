import './project/polyfil'
import './project/libs'
import './project/api'
import './project/project-components'
import './styles/styles.scss'
import { BrowserRouter as Router } from 'react-router-dom'
import { createBrowserHistory } from 'history'
import ToastMessages from './project/toast'
import routes from './routes'
import Utils from 'common/utils/utils'
import Project from 'common/project'
import AccountStore from 'common/stores/account-store'
import data from 'common/data/base/_data'

window.Utils = Utils
window.openModal = require('./components/modals/base/Modal').openModal
window.openModal2 = require('./components/modals/base/Modal').openModal2
window.openConfirm = require('./components/modals/base/Modal').openConfirm

const rootElement = document.getElementById('app')

const params = Utils.fromParam()

if (params.token) {
  API.setCookie('t', params.token)
  document.location = document.location.origin
}

// Render the React application to the DOM
const res = API.getCookie('t')

const event = API.getEvent()
if (event) {
  try {
    data
      .post('/api/event', JSON.parse(event))
      .catch(() => {})
      .finally(() => {
        API.setEvent('')
      })
  } catch (e) {}
}

const isInvite = document.location.href.includes('invite')
const isOauth = document.location.href.includes('/oauth')
if (res && !isInvite && !isOauth) {
  AppActions.setToken(res)
}

function isPublicURL() {
  const pathname = document.location.pathname

  const publicPaths = [
    '/',
    '/404',
    '/home',
    '/password-reset',
    '/maintenance',
    '/github-setup',
    '/oauth',
    '/register',
    '/saml',
    '/signup',
    '/login',
  ]

  return publicPaths.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  )
}

setTimeout(() => {
  const browserHistory = createBrowserHistory({
    basename: Project.basename || '',
  })

  // redirect before login
  if (!isPublicURL() && !AccountStore.getUser()) {
    API.setRedirect(
      document.location.pathname + (document.location.search || ''),
    )
    browserHistory.push(
      `/?redirect=${encodeURIComponent(
        document.location.pathname + (document.location.search || ''),
      )}`,
    )
  }

  ReactDOM.render(
    <Router basename={Project.basename || ''}>{routes}</Router>,
    rootElement,
  )
}, 1)

// Setup for toast messages
ReactDOM.render(<ToastMessages />, document.getElementById('toast'))

if (E2E) {
  document.body.classList.add('disable-transitions')
}
const isWidget = document.location.href.includes('/widget')
if (!E2E && Project.crispChat && !isWidget) {
  window.$crisp = []
  window.CRISP_WEBSITE_ID = Project.crispChat
  ;(function () {
    const d = document
    const s = d.createElement('script')
    s.src = 'https://client.crisp.chat/l.js'
    s.async = 1
    d.getElementsByTagName('head')[0].appendChild(s)
  })()
}
