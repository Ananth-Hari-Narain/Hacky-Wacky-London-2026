## Set up information ##
You can authenticate using either **SSH** (recommended) or **Personal Access Token (PAT) over HTTPS**.

---

### Option A: SSH (Secure & One-Time Login)

1. Check for existing keys (on Linux / Mac)
ls ~/.ssh

If id_ed25519.pub does not exist, generate a new key.

2. Generate SSH key
ssh-keygen -t ed25519 -C "your_email@example.com"

Press Enter to accept defaults. Optionally add a passphrase.

3. Start SSH agent
eval "$(ssh-agent -s)"
4. Add key to agent
ssh-add ~/.ssh/id_ed25519
5. Copy public key
cat ~/.ssh/id_ed25519.pub

Copy the output.

6. Add key to GitHub

Go to Settings → SSH and GPG keys → New SSH key. Paste the key and save.

7. Test connection
ssh -T git@github.com

You should see a success message.

8. Clone repository using SSH
git clone git@github.com:USERNAME/REPO.git

### Option B: Personal Access Token (PAT) (Fast & Simple)
1. Create a fine-grained PAT

Go to Settings → Developer settings → Personal access tokens → Fine-grained tokens.

Click Generate new token.

Repository access → Only select repositories → choose this repo.

Permissions:

Contents: Read & Write

Pull requests: Read & Write

Set expiration (7–30 days) and generate the token. Copy it.

2. Clone repository using HTTPS
git clone https://github.com/USERNAME/REPO.git
3. Authenticate

When prompted:

Username: GitHub username

Password: paste your PAT

Your system will usually save it automatically.
