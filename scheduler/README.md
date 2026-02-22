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



### IDEAS:

# 🎓 Project MVP: Adaptive Learning & Scheduling Engine

A high-fidelity, gamified revision and scheduling platform that leverages a Vector DB and student performance history to maximize learning efficiency per unit of time.

---

## 🚀 Core Features

### 🧠 1. Revision Mode (The Problem Selector)
The system acts as an automated private tutor by knowing exactly what you need to work on.
* **Performance-Based Selection:** Automatically SELECTs the problem that maximizes your "learning per unit time" based on your history and topic mastery.
* **Submission & Feedback:** Upload your solutions for automatic marking. The system provides feedback and updates your strengths/weaknesses profile.
* **Synthetic Problem Generation:** If you run out of official practice questions, the AI generates similar problems targeting your specific weak areas.
* **Full Context Access:** The engine has RAG access to all lecture notes, mark schemes, and examiner reports.
* **GAMIFIED:** Built-in mechanics to keep engagement high through progression and rewards.

### 📅 2. "Daily" Mode & Dynamic Scheduling
A living calendar designed to adapt to the reality of student life.
* **2-Week Rolling Calendar:** Visualizes your upcoming workload and focus areas.
* **Global Context Overrides:** Large text box for broad changes (e.g., "I have a trip planned this weekend," or "I'm skipping lectures today").
* **Event-Specific Notes:** Small text boxes for each event to provide the scheduler with extra nuance.
* **Curriculum Sync:** Dedicated text boxes for every lecture to track exactly what content has been reached, ensuring the schedule stays tailored to your actual progress.

---

## 🏗️ Vector DB Architecture

The system utilizes a dual-layer Vector Database approach to balance static curriculum data with dynamic user performance.

### A. Centralized Curriculum (Global)
* **Official Papers:** All past papers, mark schemes, and examiner reports.
* **Practice Sets:** All problem sheets and solutions (e.g., PMT resources).
* **Course Material:** All lecture notes for every module.

### B. Per-User Data