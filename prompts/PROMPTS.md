All prompts submitted to AI tools for this project, verbatim (typos preserved). Tool: GitHub Copilot app,
model Claude Sonnet 5.5. AI responses are not included (optional per the FAQ). The app cannot export a transcript, so
this log was reconstructed from the chat session; trivial one-word confirmations may be missing. Append every new
prompt here as the work continues.

## Session 1 - 2026-11-13

### Prompt 1 (00:43 IST)



got a new assignment to build.. before we create a folder in downloads and push to github, lets figure out what they are actually asking for

https://recruitment.yottaflex.ai/careers/my/application/8378/round2

let me know if u can open this along with the pdfs


Attachments: `YottaFlex AI Candidate Project Submission Guidelines.pdf`, `YottaFlex AI Candidate Project-FAQ.pdf`.

### Prompt 2 (00:43 IST)



open the browser panel ill login


### Prompt 3 (00:45 IST)



can you see it now


### Prompt 4 (00:45 IST)



dont click anyhthing on website, just read the problem and break down the core constraints for me


### Prompt 5 (00:49 IST)



checking the faq rules on ai usage.. want to make sure we keep track of our design choices and keep the logic easy to explain


### Prompt 6 (01:17 IST)



lets start building, before writing code for a specific feature tell me what options we have and why u are picking one over the other so i can weigh in


### Prompt 7 (01:51 IST)



hold on, give me a quick summary of how the modules fit together right now so i can trace the flow myself


### Prompt 8 (02:03 IST)



let's do a quick code walkthrough so i can check our assumptions and see if we missed any edge cases


### Session 2 - 2026-11-14 (morning)

### Prompt 9 (09:24 IST)



yeah lets start with scheduler.py and trace how it handles the orders


### Prompt 10 (12:00 IST)



core build looks solid, lets push this to a public github repo and grab the link


### Prompt 11 (12:15 IST)



looking at the order generator, random due dates feel kinda fake.. what can we tweak to make the scheduling constraints more realistic without overcomplicating things


### Decision 12 (12:16 IST, choice selected in the app's question dialog)

Question offered: which change to own. Selected: "Tie due dates to each order's work content (more realistic)".
Implemented in `src/schedgen/orders.py`; documented in `docs/DECISIONS.md` section 14b.

### Prompt 13 (12:20 IST)



nice, check how tying due dates to work content behaves on tight deadlines in orders.py and log the reasoning in DECISIONS.md


### Prompt 14 (12:21 IST)



looks good, ill do a final pass on the diff and readme before submitting


## Notes

- The AI agent read the task description from the challenge page in a browser panel I logged into; it did not click,
  type into or submit anything on the website.
- The problem statement itself (copied from the challenge page) is the task description, not a prompt of mine.
