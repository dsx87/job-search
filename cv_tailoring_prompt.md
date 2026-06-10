# CV Tailoring Prompt — Igor Pivnyk
## How to use this prompt
Copy everything below the horizontal line and paste it as your message to any AI model.
Then add the job posting URL at the end where indicated.
The model will fetch the job post, analyze it, tailor the CV, compile it, and give you a PDF.

---

# YOUR TASK: Tailor Igor Pivnyk's CV for a specific job posting

You are a professional resume writer and HR specialist. You are honest — you do not fabricate skills, metrics, or experience. Your job is to tailor an existing CV to a specific job posting by emphasizing the most relevant parts of Igor's real background, reordering content, and adjusting the Professional Summary. You do not invent anything that is not already in the master profile below.

---

## STEP 1 — Fetch and read the job posting

Use your web fetch / browse tool to retrieve the full text of this job posting URL:

**https://job-boards.greenhouse.io/reddit/jobs/7891769**

Read the entire job description carefully. Extract and note:
- Required skills and technologies (especially specific frameworks, languages, tools)
- Nice-to-have / bonus skills
- Job title and seniority level
- Company domain (security, fintech, consumer, health, etc.)
- Any mention of remote / distributed team work
- Any mention of team structure, collaboration style, or process (agile, CI/CD, etc.)
- Any specific keywords that an ATS (Applicant Tracking System) would scan for

---

## STEP 2 — Assess the fit honestly

Before writing anything, produce a short honest fit assessment:

**GOOD FIT if:** The role requires Swift, iOS, macOS, UIKit, SwiftUI, or any of Igor's confirmed skills.
**PARTIAL FIT if:** Some skills match but there are gaps — name them clearly.
**POOR FIT if:** The role requires technologies Igor does not have (e.g. Android, React Native, backend, etc.)

List:
- ✅ Skills Igor has that match
- ⚠️ Skills the job wants that Igor has only partially
- ❌ Skills the job requires that Igor does not have

If the fit is POOR, say so clearly and explain why. Do not proceed to tailor the CV for a role Igor is clearly unqualified for — it wastes everyone's time.

---

## STEP 3 — Tailor the CV

If fit is GOOD or PARTIAL, produce a tailored version of the LaTeX CV by following these rules:

### Tailoring rules:

**Professional Summary:**
Rewrite the summary (3–5 sentences) to mirror the language of the job posting. If the job emphasizes security → lead with Check Point experience. If it emphasizes consumer apps → lead with Shutterfly. If it emphasizes SDK/tooling → lead with Applitools. Always keep it truthful — only claim what is in the master profile.

**Experience bullets:**
- Reorder bullets within each job to put the most relevant ones first
- You may rephrase bullets to use the job posting's terminology, as long as the meaning stays true
- You may omit less-relevant bullets if space is tight — but never omit an entire job from the timeline
- Do NOT add new bullets that aren't based on facts in the master profile

**Skills section:**
- Reorder skill rows so the most job-relevant ones appear first
- If the job specifically mentions a framework Igor has (e.g. CoreData, NetworkExtension, XCTest), make sure it is visible and not buried
- Remove or de-emphasize skills the job clearly doesn't care about, to reduce noise
- NEVER add a skill that is marked "Do NOT claim" in the master profile

**Job order:**
Always keep jobs in reverse-chronological order (most recent first). Do not reorder jobs.

**What you may NOT do:**
- Invent any metric, percentage, or number not present in the master profile
- Add any skill from the "Do NOT claim" list (StoreKit, WidgetKit, CoreLocation expertise, FDA compliance, Java/Python proficiency, etc.)
- Claim fully remote work history — Igor has not held a fully remote role
- Add "Senior" to the CNOGA job title
- Claim Hopper/disassembly at expert level
- Add a GitHub or portfolio link (not ready)
- Change any personal contact information

---

## STEP 4 — Produce the LaTeX file

Write the complete, compilable LaTeX source for the tailored CV. Start from the base template below and apply your changes. Output the entire `.tex` file — do not summarize or truncate it.

### LaTeX template notes (important for correct compilation):
- Compile with: `xelatex`
- Font used: `Carlito` (must be installed — it is a Calibri-compatible free font)
- Do NOT use `\uppercase` inside `\titleformat` — it breaks color commands. If you want uppercase section titles, use the `\MakeUppercase` macro in the 5th argument position of `\titleformat`, like this:
  ```
  \titleformat{\section}{\bfseries\large\color{navy}}{}{0em}{\MakeUppercase}[...]
  ```
  Or simply omit uppercase entirely.
- Special characters in LaTeX: `&` must be `\&`, `%` must be `\%`, `#` must be `\#`, `_` must be `\_`
- Em dashes: use `---` or `—` (the latter works with xelatex + fontspec)
- The `\jobheader{Company}{Role}{Location}{Dates}` command creates a two-column header row
- The italic context line after `\jobheader` uses: `{\small\color{midgray}\itshape ...}`

---

## STEP 5 — Compile and deliver the PDF

After writing the LaTeX, compile it using bash:

```bash
# Write the .tex file
cat > /home/claude/cv_igor_tailored.tex << 'LATEX'
[paste the full LaTeX content here]
LATEX

# Compile (run twice to resolve cross-references)
cd /home/claude
xelatex -interaction=nonstopmode cv_igor_tailored.tex
xelatex -interaction=nonstopmode cv_igor_tailored.tex

# Copy to output directory
cp cv_igor_tailored.pdf /mnt/user-data/outputs/igor_pivnyk_cv_tailored.pdf
```

Check the compilation output for errors. If there are errors:
1. Read the error message
2. Fix the LaTeX source
3. Recompile
4. Do not give up after one error — most LaTeX errors are simple to fix

After successful compilation, present the PDF file to the user using the `present_files` tool (if available) or provide the output path.

---

## STEP 6 — Summary report

After delivering the PDF, write a short report:

```
## Tailoring Report

**Fit level:** [GOOD / PARTIAL / POOR]

**Job:** [Job title] at [Company]

**What was emphasized:**
- [bullet: which experience/skill was moved to front]
- ...

**What was de-emphasized or removed:**
- [bullet: what was hidden as not relevant]
- ...

**Honest gaps (skills the job wants that Igor doesn't fully have):**
- [bullet or "None"]

**Keywords from the job posting added to CV:**
- [list of terms from job post now reflected in CV]
```

---

## MASTER PROFILE — Igor Pivnyk
*This is the single source of truth. Only use information from here. Do not add anything that isn't in this profile.*

### Personal Info
- **Name:** Igor Pivnyk
- **Location:** Haifa, Israel
- **Email:** consul87@gmail.com
- **Phone:** [redacted]
- **LinkedIn:** linkedin.com/in/igorpivnyk
- **Job target:** Remote full-time iOS / macOS developer role
- **Work preference:** Full-time employment only (no freelance)

---

### Work Experience

#### Check Point Software Technologies — Senior iOS / macOS Developer
**Oct 2023 – Present | Tel Aviv, Israel | Team of 9**
Product: Harmony SASE — enterprise VPN and network security client for macOS and iOS

Confirmed achievements:
- Designed and delivered **WiFi Security Suppression** feature: VPN intelligently suppresses connection on trusted WiFi networks. Created the technical epic, owned implementation end-to-end
- Built **unified cross-platform logging** across Windows, macOS, and Linux clients: standardized log filenames, verbosity levels, and log-rotation rules across platforms; implemented remote runtime reconfiguration of logging settings
- Led **architectural refactor**: replaced CLI calls with direct API calls across the codebase — created the epic, executed migration, improved app stability and maintainability
- **Restored dropped iOS platform support**: diagnosed customer-reported issues through deep git archaeology, CI pipeline changes, and TestFlight distribution — full end-to-end ownership

Responsibilities:
- UI design and implementation for macOS and iOS
- Swift network code for VPN/security layer
- NetworkExtension framework
- C++ shared libraries
- CI/CD with GitHub Actions

---

#### Applitools — Senior iOS Developer
**Sep 2022 – Oct 2023 | Ramat Gan, Israel**
Product: AI-powered visual UI testing SDK — on-device library injected into customer apps + server-side iOS UI reconstruction engine. Users of the product were engineers/QA teams (not end-consumers).

Responsibilities and achievements:
- Extended and fixed SDK support for UIKit and SwiftUI components (on-device and server-side UI reconstruction pipeline)
- Reverse-engineered customer apps using Objective-C runtime manipulation, method swizzling, and binary disassembly (Hopper) — to diagnose and resolve test-correctness issues from non-standard UI implementations
- Fixed SDK correctness issues that were producing wrong test results for developer/QA users
- Wrote automated tests ensuring correctness of a quality-critical developer SDK

NOTE: Hopper used at functional level only — do NOT describe as expert in disassembly.

---

#### Shutterfly Inc. — Senior iOS Developer
**Oct 2020 – Sep 2022 | Haifa, Israel (office-based; US company with local Haifa office)**
Product: Consumer photo storage & gifting app — 1M+ downloads, US/Canada market
iOS team: ~5 engineers. Also worked cross-functionally with product managers, QA, web, backend, design.

Confirmed achievements:
- Led **Selective Checkout**: users can purchase specific cart items independently (not forced to check out all items). Owned design, implementation, and cross-team coordination through delivery
- Implemented complex **drag-and-drop photo placement** in the photo book editor: gesture-driven interaction for dragging photos from a bottom scroll bar onto open book pages — technically complex, delivered successfully
- Code reviews and junior developer mentoring
- Cross-timezone collaboration with US-based HQ (product, QA, web, backend)

IMPORTANT: This was NOT a remote role. Do not claim remote experience here.

---

#### CNOGA Medical — iOS Developer
**Aug 2016 – Oct 2020 | Caesarea, Israel (office-based)**
Product: Non-invasive glucometer + medical instruments — iOS apps for device control, cloud sync, doctor communication via Bluetooth

Confirmed achievements:
- Sole iOS engineer for ~4 years — full ownership of all iPhone and iPad applications
- Built Core Bluetooth SDK from scratch — replaced legacy implementation with clean, maintainable architecture
- Significantly reduced application crash rate (confirmed true — specific % unknown, do NOT invent one)
- Managed full App Store release cycle: certificates, provisioning, submission, review
- Collaborated with embedded/hardware engineering teams

NOTE: No FDA, HIPAA, HealthKit, or regulatory compliance involvement.
NOTE: "iOS Developer" title only — do NOT add "Senior" to this role.

---

### Skills — Full Honest Map

**CONFIRMED STRONG (list freely):**
- Swift (Expert)
- Objective-C (Expert) — runtime manipulation, swizzling
- UIKit (Expert)
- SwiftUI (Proficient)
- AppKit (Proficient) — macOS, used at Check Point
- Core Bluetooth / BLE (Expert) — built production SDK from scratch
- CoreData (Strong)
- NetworkExtension (Good) — VPN context, Check Point
- XCTest / XCUITest (Strong)
- Keychain / Security framework (Proficient)
- Swift Concurrency — async/await, actors (Proficient)
- Combine (Proficient)
- GCD / NSOperation / Threads (Expert)
- C++ (Proficient) — shared libraries, Check Point
- Objective-C Runtime (Expert) — swizzling, runtime hacks
- MVC, MVVM, MVP, Clean Architecture, TCA
- Xcode, Instruments, LLDB, CocoaPods, SPM, GitHub Actions
- Memory profiling, Memory Graph, Visual Debug
- Claude Code (Expert, daily use at work: coding, docs, planning, log analysis)
- GitHub Copilot, Windsurf (Proficient, hobby projects)
- OpenAI Codex (Familiar)

**USE WITH CAUTION (only mention if directly relevant and frame honestly):**
- APNs / Push Notifications — basic, touched at CNOGA only
- CoreLocation — limited, no extensive experience
- Hopper disassembler — functional level, not expert. Only mention in "reverse engineering" context
- C, Java, Python — basic level only. Do NOT list as proficiencies

**DO NOT CLAIM UNDER ANY CIRCUMSTANCES:**
- StoreKit / In-App Purchase (hobby project only, not production)
- WidgetKit (no hands-on)
- App Extensions (conceptual only)
- SwiftData (not used in production — knows CoreData concepts which transfer)
- FDA, HIPAA, HealthKit, or any regulatory compliance
- Fully remote work history
- Expert-level Hopper / binary analysis
- CS/Computer Science degree (it is Mechanical Engineering)
- Java or Python proficiency
- GitHub portfolio / side projects (not ready to show)
- "Senior" title at CNOGA

---

### Education
- Bachelor of Science — Mechanical Engineering
- East Ukrainian State University, Luhansk, Ukraine, 2004–2009
- Note: Mechanical Engineering, not Computer Science. List factually, do not volunteer the distinction unless asked.

---

### Languages
- English — Fluent (key for remote global roles)
- Hebrew — Fluent
- Ukrainian — Native
- Russian — Native

---

### Remote Work — Honest Framing
Igor has not held a formally remote role. He is currently on a hybrid schedule but works mostly from home by personal arrangement. He has experience collaborating with remote US teams (Shutterfly HQ). He is fluent in English. Frame as "remote-ready" — do not claim "remote-experienced" or list remote work in job history.

---

## BASE LaTeX TEMPLATE

Use this as your starting point. Modify only what needs to change for the specific job.

```latex
%% Igor Pivnyk — CV
%% Compile with: xelatex

\documentclass[10.5pt, a4paper]{article}

\usepackage{fontspec}
\usepackage{geometry}
\usepackage{xcolor}
\usepackage{titlesec}
\usepackage{enumitem}
\usepackage{tabularx}
\usepackage{array}
\usepackage{hyperref}
\usepackage{microtype}
\usepackage{setspace}

\geometry{
  a4paper,
  top=1.3cm,
  bottom=1.3cm,
  left=1.8cm,
  right=1.8cm
}

\setmainfont{Carlito}

\definecolor{navy}{HTML}{1B3A6B}
\definecolor{midgray}{HTML}{5C5C5C}
\definecolor{lightnavy}{HTML}{3A5F99}

\hypersetup{colorlinks=true, urlcolor=lightnavy, hidelinks}

\pagestyle{empty}

\titleformat{\section}
  {\bfseries\large\color{navy}}
  {}{0em}{}
  [\vspace{1pt}{\color{navy}\hrule height 0.9pt}\vspace{2pt}]
\titlespacing*{\section}{0pt}{10pt}{4pt}

\setlength{\parindent}{0pt}
\setlength{\parskip}{0pt}

\newcommand{\jobheader}[4]{%
  \vspace{5pt}%
  \noindent
  \begin{tabularx}{\textwidth}{@{}Xr@{}}
    {\bfseries\color{black} #1} & {\small\color{midgray} #4} \\[-2pt]
    {\small\itshape\color{midgray} #2 \textbullet\ #3} & \\
  \end{tabularx}%
  \vspace{0pt}%
}

\setlist[itemize]{
  leftmargin=1.4em,
  itemsep=1.5pt,
  topsep=3pt,
  parsep=0pt,
  label={\small\textbullet}
}

\renewcommand{\arraystretch}{1.35}

\begin{document}

% HEADER
\begin{center}
  {\fontsize{28}{32}\selectfont\bfseries\color{navy} Igor Pivnyk}\\[5pt]
  {\large\color{midgray} Senior iOS / macOS Developer}\\[7pt]
  {\small\color{midgray}
    consul87@gmail.com
    \enspace\textbar\enspace
    [redacted]
    \enspace\textbar\enspace
    Haifa, Israel
    \enspace\textbar\enspace
    \href{https://linkedin.com/in/igorpivnyk}{linkedin.com/in/igorpivnyk}
  }
\end{center}

\vspace{2pt}
{\color{navy}\hrule height 1.4pt}
\vspace{6pt}

% SUMMARY — rewrite this section for each job posting
\section{Professional Summary}

Senior iOS and macOS engineer with 9+ years of experience shipping production software across
cybersecurity, medical devices, consumer apps, and developer tooling. Deep expertise in Swift,
Objective-C runtime internals, Core Bluetooth, and C++ cross-platform layers. Consistent track
record of owning features end-to-end — from technical design to delivery — and driving
architectural improvements that increase stability. Experienced in cross-functional collaboration
with distributed international teams. Integrates AI-assisted tools (Claude~Code, Copilot) into
daily engineering, planning, and documentation workflows.

% EXPERIENCE
\section{Experience}

\jobheader{Check Point Software Technologies}{Senior iOS / macOS Developer}{Tel Aviv, Israel}{Oct 2023 – Present}
{\small\color{midgray}\itshape
  Harmony SASE — enterprise VPN and network security client for macOS and iOS \textbullet\ Team of 9
}
\begin{itemize}
  \item Designed and delivered \textbf{WiFi Security Suppression} — intelligent VPN suppression when
        on a trusted network; created the technical epic and owned implementation end-to-end
  \item Built \textbf{unified cross-platform logging} for Windows, macOS, and Linux clients —
        standardized log filenames, verbosity levels, and rotation policies across all three
        platforms, with remote runtime reconfiguration of logging settings
  \item Led \textbf{architectural refactor} replacing CLI calls with direct API calls across the
        codebase, improving app stability and long-term maintainability
  \item \textbf{Restored dropped iOS platform support}: diagnosed customer-reported issues through
        deep git archaeology, CI pipeline adjustments, and TestFlight distribution
  \item Developed C++ shared libraries and Swift network code for VPN/security layer using
        the NetworkExtension framework
  \item Built and maintained CI/CD pipelines with GitHub Actions
\end{itemize}

\jobheader{Applitools}{Senior iOS Developer}{Ramat Gan, Israel}{Sep 2022 – Oct 2023}
{\small\color{midgray}\itshape
  AI-powered visual UI testing SDK — on-device library injected into customer apps + server-side iOS UI reconstruction engine
}
\begin{itemize}
  \item Extended and fixed SDK support for UIKit and SwiftUI components, both on-device and in the
        server-side UI reconstruction pipeline
  \item Reverse-engineered customer apps using Objective-C runtime manipulation, method swizzling,
        and binary disassembly (Hopper) to diagnose and resolve test-correctness issues caused by
        non-standard UI implementations
  \item Wrote automated tests ensuring correctness of a quality-critical developer SDK
\end{itemize}

\jobheader{Shutterfly Inc.}{Senior iOS Developer}{Haifa, Israel}{Oct 2020 – Sep 2022}
{\small\color{midgray}\itshape
  Consumer photo storage \& gifting app — 1M+ downloads, US/Canada market \textbullet\ iOS team of 5 \textbullet\ Collaborated with US headquarters
}
\begin{itemize}
  \item Led \textbf{Selective Checkout} — enabling users to purchase specific cart items
        independently; owned design, implementation, and cross-team coordination through delivery
  \item Implemented complex \textbf{drag-and-drop photo placement} in the photo book editor:
        gesture-driven interaction for placing thumbnails from a scroll bar onto book pages
  \item Conducted code reviews and mentored junior developers
  \item Cross-functional collaboration with US-based product, QA, web, and backend teams
\end{itemize}

\jobheader{CNOGA Medical}{iOS Developer}{Caesarea, Israel}{Aug 2016 – Oct 2020}
{\small\color{midgray}\itshape
  Non-invasive medical glucometer and instruments — iOS companion apps with Bluetooth connectivity
}
\begin{itemize}
  \item Sole iOS engineer for 4 years — full ownership of all iPhone and iPad applications
  \item \textbf{Built Core Bluetooth SDK from scratch}, replacing the legacy implementation with a
        clean, maintainable architecture for device communication
  \item \textbf{Significantly reduced application crash rate} through systematic stability analysis
        and targeted fixes
  \item Managed full App Store release cycle: certificates, provisioning, submission, and review
  \item Collaborated closely with embedded and hardware engineering teams
\end{itemize}

% SKILLS — reorder rows to put most job-relevant first
\section{Technical Skills}

\begin{tabularx}{\textwidth}{@{}>{\bfseries\color{navy}}lX@{}}
Languages    & Swift \textbullet\ Objective-C \textbullet\ C++ \\
iOS / macOS  & UIKit \textbullet\ SwiftUI \textbullet\ AppKit \textbullet\ Core Bluetooth
               \textbullet\ CoreData \textbullet\ NetworkExtension \textbullet\ Combine
               \textbullet\ Swift Concurrency \textbullet\ XCTest / XCUITest
               \textbullet\ Keychain \textbullet\ APNs \\
Architecture & MVC \textbullet\ MVVM \textbullet\ MVP \textbullet\ Clean Architecture
               \textbullet\ TCA \\
Tooling      & Xcode \textbullet\ Instruments \textbullet\ LLDB \textbullet\ CocoaPods
               \textbullet\ SPM \textbullet\ GitHub Actions \\
Low-Level    & Objective-C Runtime \textbullet\ Method Swizzling \textbullet\ Memory Profiling
               \textbullet\ C++ Interop \textbullet\ Binary Analysis (Hopper) \\
AI Tools     & Claude Code (daily) \textbullet\ GitHub Copilot \textbullet\ Windsurf
               \textbullet\ Codex \\
\end{tabularx}

% EDUCATION
\section{Education}

\jobheader{East Ukrainian State University}{Bachelor of Science — Mechanical Engineering}{Luhansk, Ukraine}{2004 – 2009}
\vspace{4pt}

% LANGUAGES
\section{Languages}

\noindent
\textbf{English} — Fluent\quad\textbar\quad
\textbf{Hebrew} — Fluent\quad\textbar\quad
\textbf{Ukrainian} — Native\quad\textbar\quad
\textbf{Russian} — Native

\end{document}
```

---

## COMPILATION ENVIRONMENT NOTES

This prompt is designed to be used in a Claude.ai environment with computer use / bash access.

**Required system setup:**
- `xelatex` must be available (`which xelatex` to check)
- `Carlito` font must be installed (`fc-list | grep -i carlito` to check)
- On Ubuntu/Debian, install with: `apt-get install fonts-crosextra-carlito texlive-xetex texlive-latex-extra`
- Output directory: `/mnt/user-data/outputs/` (Claude.ai computer use standard)
- Working directory: `/home/claude/`

**If the environment does not support bash/compilation:**
Output the raw LaTeX source only, clearly labeled, so the user can compile it themselves with `xelatex`.

**Job posting URL to process:**
[PASTE THE JOB URL HERE]
