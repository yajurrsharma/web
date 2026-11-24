# Publishing the Android app on Google Play

The store is already an installable web app (PWA): it has a web manifest, icons, a service worker
with an offline page, and an endpoint for Digital Asset Links. The Play Store app is a
**Trusted Web Activity (TWA)**: a thin Android wrapper that opens your live site full-screen.
Products, prices, orders and invoices stay in sync because it's the same site.

## Why a TWA (not a separate native app)

- One codebase and one admin. Website changes reach the app without a new Play release.
- Physical goods (agarbatti, dhoop) may be paid through your own gateway, so Razorpay works as on
  the website. Google Play Billing is for digital goods.
- No React Native/Flutter project to maintain.

## Requirements

1. The site live on your domain over **HTTPS** (DEPLOYMENT §16).
2. A **Google Play Console** developer account (one-time registration fee; identity verification
   required). Check Google's current requirements when you enrol. New personal accounts may need to
   run a closed test with real testers before publishing to production.
3. Java (JDK 17) and Node.js on your PC, used only to build the app.

## Steps

1. **Check the PWA**: open your live site in Chrome → DevTools → *Application* → Manifest should
   show no errors, and the service worker should be active.
2. **Generate the app** with Bubblewrap (Google's tool):

   ```powershell
   npm i -g @bubblewrap/cli
   bubblewrap init --manifest https://www.yourdomain.in/manifest.webmanifest
   bubblewrap build
   ```

   It asks for an app ID such as `in.yourdomain.store` and creates a signing key. **Back the key
   file and its passwords up safely**: you can't update the app without them. (PWABuilder.com is a
   web alternative that produces the same package.)
3. **Link the app to the site** (removes the browser address bar). Get the SHA-256 fingerprint of the
   app signing key. With Play App Signing, use the one shown in Play Console → *Setup → App signing*.
   Put both values in the server `.env`, then reload:

   ```ini
   ANDROID_PACKAGE_NAME=in.yourdomain.store
   ANDROID_SHA256_FINGERPRINTS=AB:CD:...:EF
   ```

   (Several fingerprints can be comma-separated, e.g. upload key and Play signing key.) Then check
   that `https://www.yourdomain.in/.well-known/assetlinks.json` shows them.
4. **Play Console**: create the app → upload the `.aab` from `bubblewrap build` to *Internal testing*
   → fill in the store listing (description, screenshots, 512×512 icon from `static/img/`, feature
   graphic), **privacy policy URL** (`https://www.yourdomain.in/p/privacy-policy/`), *Data safety*
   form (you collect name, email, phone, address and purchase history; payments go through Razorpay),
   content rating and target audience.
5. Test on real phones (internal → closed testing), then promote to **Production**.

## Updating

Website changes appear in the app straight away. Rebuild and upload a new `.aab` (incrementing
the version) only when you change the app's name, icon, colours or package settings.

## Later options

- Push notifications for order updates (Firebase Cloud Messaging) — needs extra work on the site.
- A fully native app — only if the TWA proves limiting.
