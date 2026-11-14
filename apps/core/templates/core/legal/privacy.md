{% comment %} The Privacy Policy (issue #96), Markdown rendered by `apps.core.views.legal`. A single newline is a line break, so every paragraph and list item is one line. Tables are not supported. When this text materially changes, bump `TERMS_VERSION` and `EFFECTIVE_DATE` in `apps/core/legal.py`. Retention periods and cookie names here mirror the code: change them together. {% endcomment %}# Privacy Policy

**Effective date:** {{ effective_date }}

This Privacy Policy explains what personal information ZipTrigo ("**ZipTrigo**", "**we**", "**us**") collects when you use the website and services at `ziptrigo.com` and its subdomains, and the ZipTrigo mobile app (together, the "**Service**"), why we collect it, and the choices you have. The Service includes QR codes, file transfer, receipts, short links, to-do lists, notes and a world clock.

ZipTrigo is operated by **{{ operator_name }}**, an individual based in the {{ operator_country }}. It is not operated by a company. For any privacy question or request, write to {{ contact_email }}.

## 1. Information we collect

### 1.1 Information you give us

- **Account information:** your email address, your name and your password. We store only a one-way hash of your password, never the password itself. We also store the preferences you set, such as your time zone, theme, and date and time format, and the date and version of the Terms of Use you accepted.
- **QR codes:** the content each code encodes (such as a URL or text) and its settings.
- **File transfers:** the files you upload with their names and sizes, the recipient email addresses, the optional message, the expiry and download-limit settings, and an optional download password (stored as a hash). If you send a transfer without an account, we collect the email address you give us so we can confirm it before the transfer is sent.
- **Receipts:** the receipts and invoices you upload or send by email, the details read from them (vendor, amount, date, category and, where present, the last 4 digits of a payment method), and your notes. For receipts sent by email we also keep the sender's address, the subject, and a copy of the message with its attachments removed.
- **Short links:** the destination address, title, tags and settings of each link, and an optional password (stored as a hash).
- **To-do:** your tasks, descriptions, comments (including earlier versions of a comment you edited), labels, reminders, saved filters, and the files you attach. For tasks added by email we keep the sender's address and the subject.
- **Notes:** the title and Markdown content of your notes, their tags, and the earlier versions that edits of a note keep (a limited number per note). If you share a note with a link, we also keep the link (with the label and expiry you set), and anyone who has the link can see the note without signing in. If someone changes a note through an editable link, we keep the earlier versions as for any edit; when that person was signed in, the version records their account, and the note's owner can see which account made it (a visitor who was not signed in appears only as someone using the link).
- **World Time:** the tabs you create and the places you add to them (a city or a time zone, and the name you give it). We do not use your device's location: the first row comes from the time zone saved on your account. If you share a time as an event, we keep its title, its start and end, a copy of the places of the tab it was made from, and its link; anyone who has the link can see them without signing in.
- **Feedback and reports:** messages you send through the Feedback page, and abuse reports about a transfer, a short link or a shared note, which include the reason, any details you write and, optionally, your email address.

### 1.2 Information from Google and Microsoft

If you choose **Sign in with Google**, on the website or in the mobile app, Google shares with us your name, email address, profile picture and a unique Google account identifier, and tells us whether your email address is verified. We store only your name, your email address and the identifier. We use them only to create your account, to sign you in, and to link Google sign-in to an existing ZipTrigo account with the same email address. We do not receive your Google password, and we do not access your Gmail, contacts, files or any other Google data.

If you choose **Sign in with Microsoft**, Microsoft shares with us your name, your email address and identifiers of your Microsoft account and its organization. We use them only for the same purposes. We do not receive your Microsoft password and do not access any other Microsoft data.

### 1.3 Information collected automatically

- **Download records:** we record the IP address and time of each download of a transfer, so the sender can see download activity and we can investigate abuse.
- **Shared notes:** when someone opens a shared note we do not record who they are; the page is not stored by search engines and is not cached. If a shared note is edited through an editable link, we record the time of the edit.
- **Shared events:** when someone opens a shared World Time event we do not record who they are; the page is not stored by search engines and is not cached. The time in the visitor's own time zone is worked out in their browser.
- **Short link clicks:** when someone follows a short link we record the time, the website they came from (its host name only), their country, their type of device, browser and operating system, and whether the visit looks automated. We do not store the visitor's IP address. We store a keyed, one-way hash of it that changes every day, used only to count unique visitors per day.
- **QR code scans:** for tracked QR codes we count how many times a code was scanned. We do not record who scanned it.
- **Abuse prevention:** we use IP addresses to enforce rate limits and usage caps. For rate limiting we store only a keyed, one-way hash, never the address itself. An abuse report stores the reporter's IP address.
- **Credit activity:** a record of the credits added to and spent from your account, and of promo codes you redeemed.
- **Notifications:** the notices the Service shows you and, if you turn on notifications on a device, that browser's push subscription (an address at the browser maker's push service, and encryption keys).
- **Sign-in records:** identifiers of the sign-in tokens issued to the mobile app and other API clients, so they can be renewed and revoked. We do not store the tokens themselves.
- **Server logs:** like most websites, our servers keep technical logs (IP address, browser user agent, requested page, time) for security and troubleshooting.

## 2. How we use your information

We use your information to:

- provide the Service: create and secure your account, sign you in, and run each of the features described above;
- send the emails and notifications the Service depends on, such as account confirmation, password reset, transfer notifications to you and your recipients, expiry notices, reminders you set, and receipts for feedback;
- meter usage and keep track of your credit balance;
- prevent and investigate fraud, spam and abuse, including handling abuse reports, taking down content and blocking senders or domains;
- keep the Service secure and working, and fix problems;
- respond to your feedback and requests;
- comply with legal obligations.

We do **not** sell your personal information, we do not use it for advertising, and we do not use your content to train AI models.

### Automated reading of your content

Two features use an AI model to read content for you. **Receipts** sends each receipt or invoice you add to an AI model to read its vendor, amount, date and category. **To-do** sends the title of a task (or the subject of an emailed task) to an AI model to find a date in it. The model is run by third-party AI providers, reached through a model-routing service; see section 4. Do not add documents to Receipts that you do not want processed this way.

### Legal bases (EEA and UK users)

We process your information to perform our contract with you (providing the Service you signed up for), for our legitimate interests (security, preventing abuse, statistics for link owners, improving the Service), to comply with legal obligations and, where we ask for it, on the basis of your consent.

## 3. Google user data

ZipTrigo's use of information received from Google APIs adheres to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements. We use the data described in section 1.2 only to provide sign-in and account features. We do not transfer it to others except as needed to provide the Service, to comply with the law, or as part of a transfer of the Service to a new operator. We do not use it for advertising or to train AI models, and no human reads it except with your permission, for security purposes, or to comply with the law.

## 4. How we share information

We share personal information only:

- **With the people you choose:** the recipients of a transfer receive an email with your name or email address, your message and a download link. Anyone who has the address of a short link, a tracked QR code, a transfer or a shared note can follow it.
- **With service providers** that process data for us:
    - Amazon Web Services (file storage, and sending and receiving email);
    - a server hosting provider in the United States;
    - Cloudflare (network security, content delivery, and the check that a sign-up is made by a person);
    - AI model providers, reached through a model-routing service (reading receipts and task titles, as described in section 2);
    - Google Safe Browsing (the destination addresses of short links are sent to Google to check them against lists of unsafe sites);
    - Google and Microsoft (sign-in, if you choose it);
    - the push services of browser makers such as Google, Mozilla, Apple and Microsoft (delivering notifications to a device where you turned them on; the content is encrypted for your browser).
- **When required by law,** or to protect the rights, safety and property of our users, the public or ZipTrigo, including responding to valid legal requests and abuse reports.
- **If the Service changes hands,** for example if it is transferred to a company formed to operate it, or as part of a merger or acquisition, subject to this Policy.

## 5. How long we keep information

- **Account information:** for as long as your account exists.
- **Transferred files:** deleted when the transfer ends, whether it expires, reaches its download limit, or is disabled or deleted by you, or by us after an abuse takedown. If a transfer is suspended because the account ran out of credits, its files are deleted after the grace period shown in the Service (7 days) unless credits are added. Unfinished uploads are deleted after about 24 hours.
- **Transfer records:** the record of a transfer (file names, sizes, recipients, download history) is kept with your account after its files are deleted, so you can see your history. IP addresses are removed from download records after 90 days.
- **Receipts, to-do items and notes:** kept until you delete them. Items in the Trash are deleted for good after the period shown in the Service (30 days unless stated otherwise). Emails you send to your Receipts or To-do address are deleted from our intake storage once processed.
- **World Time:** tabs and places are kept until you delete them. A shared event is kept until you delete it, and is deleted automatically some time after it ends (30 days unless stated otherwise); its link then stops working.
- **Short links:** kept until you delete them. The address of a deleted link is retired and never reused. Individual click records are deleted after about 90 days; daily totals without any visitor identifier are kept with the link.
- **Notifications:** deleted after 90 days.
- **Email confirmation codes:** deleted after about 30 days.
- **Credit history, feedback and abuse reports:** kept as long as needed for their purpose and to handle abuse.
- **Server logs:** kept for a limited time.

When an account is deleted, we delete its personal information and content, except what we must keep for legal reasons or to handle abuse. One thing is kept on purpose: a keyed, one-way fingerprint of each email address that was confirmed on the Service. It contains no readable address and is used only to grant the free welcome credits once per address.

## 6. Cookies and browser storage

We use only the cookies the Service needs to work:

- `sessionid` keeps you signed in. It lasts until you sign out, or 2 weeks.
- `csrftoken` protects forms against cross-site request forgery. It lasts 1 year.
- `ft_anon_id` enforces the daily limits for transfers sent without an account. It lasts about 2 years.

We also keep a few values in your browser's own storage, such as whether notifications are on for this device. We do not use advertising or third-party tracking cookies, and we do not run third-party analytics. Cloudflare may set strictly necessary security cookies.

## 7. Security

We protect your information with encryption in transit (HTTPS), hashed passwords, private file storage reachable only through short-lived signed links, and access controls on our systems. No method of transmission or storage is completely secure. We will notify you of a breach as required by law.

## 8. Your rights and choices

Depending on where you live, you may have the right to access, correct, delete or export your personal information, to object to or restrict certain processing, and to withdraw consent at any time. You can:

- update your name, time zone and preferences on the **Account** page;
- delete your transfers, receipts, short links, tasks, notes, world clock tabs and files in the Service at any time;
- request a copy of your data, or the deletion of your account and its data, by writing to {{ contact_email }} from the email address of your account. We act on a deletion request within 30 days.

If you are in the EEA or the UK, you can also lodge a complaint with your local data protection authority.

## 9. Children

The Service is not directed to children. You must be at least **16 years old** to create an account. If you believe a child has given us personal information, contact us and we will delete it.

## 10. Where information is processed

We store and process information in the **United States**. If you use the Service from another country, your information will be transferred there, where data protection laws may differ from those of your country.

## 11. Changes to this Policy

We may update this Policy from time to time. If we make material changes, we will update the effective date above and ask you to review and accept the updated documents the next time you sign in.

## 12. Contact

{{ operator_name }}, {{ operator_country }}
Email: {{ contact_email }}
