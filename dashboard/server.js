// dashboard/server.js
const express = require('express');
const session = require('express-session');
const fetch = require('node-fetch');
const app = express();

const CLIENT_ID = process.env.DISCORD_CLIENT_ID;
const CLIENT_SECRET = process.env.DISCORD_CLIENT_SECRET;
const REDIRECT_URI = process.env.REDIRECT_URI || 'http://localhost:3000/callback';

app.use(session({
  secret: 'change_this_secret',
  resave: false,
  saveUninitialized: false
}));

// Serve static files (dashboard frontend)
app.use(express.static('public'));

// Discord OAuth2 login URL
app.get('/login', (req, res) => {
  const discordAuthUrl = `https://discord.com/api/oauth2/authorize?client_id=${CLIENT_ID}&redirect_uri=${encodeURIComponent(REDIRECT_URI)}&response_type=code&scope=identify%20guilds`;
  res.redirect(discordAuthUrl);
});

// OAuth2 callback route
app.get('/callback', async (req, res) => {
  const code = req.query.code;
  if (!code) return res.send('No code provided');

  // Exchange code for access token
  const data = new URLSearchParams({
    client_id: CLIENT_ID,
    client_secret: CLIENT_SECRET,
    grant_type: 'authorization_code',
    code,
    redirect_uri: REDIRECT_URI,
    scope: 'identify guilds'
  });

  const tokenRes = await fetch('https://discord.com/api/oauth2/token', {
    method: 'POST',
    body: data,
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
  });
  const token = await tokenRes.json();

  if (token.error) return res.send('Error getting token');

  // Get user info
  const userRes = await fetch('https://discord.com/api/users/@me', {
    headers: { Authorization: `Bearer ${token.access_token}` }
  });
  const user = await userRes.json();

  req.session.user = user;
  res.redirect('/');
});

// Simple auth middleware
function checkAuth(req, res, next) {
  if (req.session.user) return next();
  res.redirect('/login');
}

// Protected dashboard page
app.get('/dashboard', checkAuth, (req, res) => {
  res.sendFile(__dirname + '/public/index.html');
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Dashboard running on port ${PORT}`));
