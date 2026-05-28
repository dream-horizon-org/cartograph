# Ascarto — User Stories

A working library of impactful scenarios across every persona Ascarto serves. Pull from this in pitches, design partner conversations, demos, and product reviews. Each story is concrete, has a clear *before/after* contrast, and demonstrates a specific product capability.

Stories are organized by persona and scenario type. Reach for whichever fits the room.

---

## Individual contributors (engineers)

**The new hire.** Priya joins as a backend engineer on Monday. Her troop is provisioned with read access to the cartograph. By Wednesday she's submitted her first PR — her chief walked her through which service to touch, summarized the relevant past decisions, and flagged the two engineers who'd want to review. *Today: three months of onboarding, half spent reading stale Confluence and asking awkward questions in #help.*

**The 3am page.** Diego is on call. A page fires at 3:14am for service-7. By the time he opens his laptop, his service-7 agent has pulled the logs, identified two probable causes, drafted a rollback, and flagged the upstream change that likely triggered it. His chief summarizes in 30 seconds: here's what we think, three options, pick one. Diego picks, the agent executes, he's back asleep by 3:25am. *Today: 90 minutes of context-gathering before he can even make a decision.*

**The cross-team feature.** A PRD drops for a feature touching six services across three teams. Diego's chief ingests it, fans out architectural questions to the affected service agents, surfaces a conflict with another team's in-flight roadmap, and pings the other team's chief to negotiate scheduling. Diego sees a summary with three decisions to make and a draft tech doc. *Today: two weeks of cross-team Slack threads, design docs, and "let's sync" meetings before any code gets written.*

**The MCP nudge.** Friday afternoon, Diego's chief says: "Three other troops have pinged me this month asking how to trigger your service-7 deployment. The Platform team would like to add it to the internal MCP catalog — shall I draft the spec for review?" Diego says yes. By Monday, every other troop in the company can deploy through service-7 without bothering Diego. *Today: the question lives in tribal knowledge forever; Diego gets pinged twice a week until he leaves.*

**The departure with continuity.** Marcus gives two weeks' notice. His troop's accumulated context — service maps, decision rationale, pitfalls, in-flight initiatives, open negotiations with other troops — is handed to whoever inherits his services. The new owner's chief absorbs the history and continues mid-stride. *Today: tribal knowledge walks out the door; the new owner discovers every landmine the hard way over six months.*

**The appraisal evidence pack.** It's review season. Priya (a year in now) opens her opt-in evidence pack: services owned, incidents she resolved, the on-call runbook her troop contributed to the cartograph, three cross-troop collaborations she led, two MCP specs she shipped. Her manager opens the same pack from their side. The conversation is about trajectory, not memory. *Today: frantic week of self-promotion docs, recency bias, and forgotten contributions.*

**The skip-meeting.** Diego has a 30-minute stakeholder sync he doesn't strictly need to attend personally. He sends his chief instead. The chief joins on his behalf with read-only context, takes notes, answers an architectural question mid-meeting by pulling the relevant decision out of the cartograph, opens Jira tickets for the action items, summarizes in two sentences afterward, and writes the decisions back to the cartograph for future troops. Diego keeps his afternoon for deep work. *Today: Diego attends, multitasks, retains nothing, blocks his deep-work afternoon.*

**The IC override.** Diego's chief surfaces a directive from his director's chief: *deprioritize the auth refactor, focus on the customer escalation work next sprint.* Diego thinks the refactor is more urgent because of compounding tech debt. He pushes back via his chief, which negotiates with the director's chief and surfaces the trade-off to the director, who agrees. The decision was made human-to-human; the chiefs removed the friction. *Today: Diego silently complies or schedules a calendar conflict to push back; either way, the trade-off isn't visible.*

---

## Managers, directors, VPs

**The Monday morning standup.** Vera is an engineering manager with eight reports across three teams. Her chief presents an aggregated standup Monday morning: who's blocked, what's at risk for the sprint, which initiatives are tracking ahead, what's drifting. She skips three of the five standups she used to attend and shows up to the two with real issues. *Today: five standups, no signal, full calendar.*

**The 1:1 prep.** Vera has 1:1s scheduled with all eight reports today. Her chief surfaces, per person, what each report has opted to share this week, the cross-team friction the cartograph shows them hitting (also opt-in), and what's worth raising that the report hasn't brought up themselves. Each conversation goes straight to actual issues. *Today: half the 1:1 is "what are you working on?", the other half is rushed.*

**The performance calibration.** Review season. Vera opens calibration with her director, comparing four engineers up for promotion. Each engineer's opt-in evidence pack is on the table — services owned, incidents resolved, knowledge contributed, cross-team work led, MCP specs shipped. The decision is grounded in data both sides agreed to share, not recency or politics. *Today: anecdotes, recency bias, the loudest advocate wins.*

**The roadmap proposal.** A VP needs to propose Q3 roadmap to the exec team. Her chief aggregates capacity estimates from each team's troop (based on actual historical throughput, current commitments, in-flight refactors), surfaces which proposed initiatives are realistic, and flags two that conflict with platform debt. The proposal goes in with real numbers, not aspirational ones. *Today: planning poker, gut feel, slipped deadlines.*

**The cross-team friction pattern.** A director's chief notices a pattern: three different engineers' troops have flagged the same dependency on Marcus's team as a bottleneck this month. The chief surfaces it as a structural issue, not a people issue, with concrete examples. The director can fix the actual problem instead of waiting for someone to complain loudly enough. *Today: friction stays invisible until it explodes in a retro.*

**The skip-level visibility.** A VP wants to know how an org-wide initiative is going across ten directors and their teams. Her chief pulls aggregated, IC-consented status from down the tree — what's shipped, what's at risk, where humans are needed. Visibility without surveillance, no chasing required. *Today: monthly status decks that nobody trusts, or calling each director in turn.*

**The skip-level safety.** A senior engineer is quietly burning out — declining throughput, escalating tone in code reviews, missed standups. The engineer's chief flags the pattern privately to the engineer first; with consent, the manager's chief surfaces it before the situation explodes. *Today: discovered by the manager weeks late, after the engineer has already decided to leave.*

---

## Indie developers and solo founders

**The solo founder.** Anika is a solo founder running three side projects. She subscribes to Ascarto's indie tier — a single troop with one agent per project, a personal cartograph, no org plumbing. The troop codes, deploys (to Vercel for the frontend, Fly for the API, Cloudflare for static assets — via MCP integrations, none of the infra run by Ascarto), monitors prod, and pings her Saturday morning that her payment service has been flaky three times this week and probably needs a retry-budget adjustment. The only tool Anika needs between idea and live URL is her troop. *Today: she's juggling code in Cursor, deploys in Vercel's dashboard, monitoring in Sentry, three Claude tabs, and a Notion she hasn't opened in a month.*

**The pre-revenue indie hacker.** Sam is shipping a side project after his day job. His troop runs continuously — drafting features overnight, deploying canaries, watching prod — and only pings him when something needs a decision. His "after-hours startup" gets twenty hours a week of background work for the cost of a Cursor subscription. *Today: he ships on weekends only and resents his day job for it.*

**The product-thinking troop.** Anika's troop isn't just engineering. Her product agent watches her PostHog analytics, Zendesk tickets, Stripe churn reasons, and competitor changelog feeds via MCPs. Monday morning it surfaces three feature opportunities ranked by expected revenue impact: *"Feature A unblocks 12 churned users this month; Feature B is what your top 5% by ARR keep asking for; Feature C closes the gap with [competitor]."* She picks one, the engineering agents start drafting. *Today: she ships what she feels like building, growth stalls, she rationalizes it as "focus."*

**The growth experiment.** Sam's marketing agent proposes a pricing test: 30% of new signups see a $19 tier instead of $25. The agent sets up the A/B test through Stripe and his analytics MCPs, watches conversion, and reports results after two weeks. If it's a win, it drafts the customer comms and the rollout plan; if it's a loss, it proposes two follow-up tests. *Today: Sam means to test pricing for six months and never does.*

**The cash-flow watch.** A solo founder's finance agent watches MRR, burn rate, runway, and Stripe payment failures. It pings the founder when something genuinely needs attention — a churn spike with a likely cause flagged, a payment-failure pattern hitting a specific card network, runway crossing a self-set threshold. *Today: the founder checks Stripe once a week and rationalizes whatever the trends are.*

**The whole-business troop.** A solo founder running a real (not side-project) software business has a troop that spans the whole company: engineering agents shipping and operating the product, a product agent prioritizing the roadmap by revenue impact, a marketing agent running growth experiments, a sales agent qualifying inbound and following up with trial users, a finance agent watching cash, a support agent triaging tickets. Same primitives as a 200-person engineering org — just sized for one human. The founder stays in command of every decision that matters; the troop runs everything that doesn't. *Today: she's the bottleneck for every function in the company, working 70-hour weeks, getting none of them right.*

---

## Cross-functional (the bigger vision)

**The PM coordinating launch.** Maya (PM) drops a PRD for a feature launching next quarter. Her chief fans out to the engineering troops of the affected teams to gather feasibility, the design troop for mockups, the marketing troop for launch positioning, the data troop for measurement plan. Two days later, Maya has a launch plan with timelines, risks, and dependencies — not a Notion doc she has to write herself. *Today: three weeks of meetings, half the dependencies discovered after work starts.*

**The marketing launch.** A marketing team's chief coordinates a feature launch: pulls release dates from engineering troops, queues blog drafts and social posts in the channels' MCPs, schedules a sales enablement session, and surfaces metrics from analytics MCPs once the launch ships. *Today: a manual launch checklist, three slipped dates, post-launch metric scramble.*

**The sales account onboarding.** A sales rep closes a large enterprise account. Her chief loops in the engineering troops responsible for the customer's required integrations, the legal troop for the contract review, the finance troop for the billing setup, and the success troop for onboarding scheduling. The customer is live in two weeks, not eight. *Today: email chains, missed handoffs, customer-visible delays.*

**The finance forecast.** A finance analyst needs a quarterly forecast across the business. Her chief pulls actual throughput from engineering troops, pipeline data from sales troops, expense patterns from operations troops, and surfaces a forecast with explicit assumptions rather than gut-feel numbers. *Today: a spreadsheet built from emails and Slack DMs over the course of a week.*

---

## How troops reach humans (and humans reach troops)

**The voice ping in the car.** Diego is driving when his chief calls his phone: "service-7 is showing elevated error rates, not paging yet but trending. Want me to draft a fix and queue it for review when you're back, or escalate to your backup?" Diego says "draft and queue." The same conversation works over Slack DM, Microsoft Teams, WhatsApp, Telegram, SMS, email, or the Ascarto native app — the chief reaches you wherever you are, on whatever channel you prefer. *Today: he finds out at his laptop in 90 minutes, by which time it's a real page.*

**The weekend WhatsApp.** Anika is hiking on a Saturday when her chief WhatsApps her: "Payment service is down for the third time today, customers are starting to complain. Want me to roll back to Friday's build?" She replies "yes" with one tap. *Today: she finds out Monday from angry customer emails.*

**The Slack-native team.** A team's chiefs all live in Slack alongside the humans. Status updates, decisions, approvals, escalations — all in the same channels engineers already use. No new tool to open, no extra notification stream. Cross-troop coordination across teams happens in shared channels, async. *Today: another tool, another tab, another set of unread badges.*

**The global async team.** A distributed engineering org spans Bangalore, Berlin, and San Francisco. Chiefs negotiate across time zones in the team's Telegram supergroup overnight — by the time the SF team logs in, half the day's blockers have already been resolved between troops asynchronously. *Today: handoff happens via a half-asleep async Slack message, half of which gets lost.*

---

## Meetings

**The standup attendee.** A team's chief joins every standup, takes notes, surfaces blockers from the previous day's troop activity, and posts a written summary to the team channel afterward — with action items and owners. Engineers can skip standups they don't strictly need to attend. *Today: 15 minutes of low-signal updates, no written record.*

**The PRD review with live context.** During a 60-minute PRD review, the PM's chief pulls real-time context from the affected service troops in the background: *"service-7 is currently in a refactor — Diego, your troop says completion is next sprint, want to confirm?"* Decisions are made with current state from the troops that actually own the work, not stale dashboard snapshots. *Today: half the meeting is people pulling up dashboards on their laptops.*

**The postmortem draft.** Within an hour of incident resolution, the involved troops' chiefs collaboratively draft a postmortem: timeline, root cause, action items, owners. The on-call engineer reviews and edits, instead of writing from scratch. The postmortem is in the cartograph by end of day. *Today: postmortem gets scheduled for next week and partially written.*

**The sprint planning.** During sprint planning, each team's chief proposes a sprint based on actual historical throughput, current commitments, and known refactors-in-flight. The team accepts, adjusts, or pushes back — but the starting proposal is grounded in data, not guesswork. *Today: planning poker by gut feel, sprint slips by week two.*

**The exec review.** Quarterly business review with the leadership team. The CEO's chief presents an aggregated status across functions — engineering velocity, sales pipeline, marketing pipeline, finance position — assembled from each function's troop. Discussion is about strategy, not status reconciliation. *Today: each function builds its own deck, two days of pre-meetings, no unified view.*

**The candidate interview.** A hiring manager is on an interview panel. Her chief silently pulls the candidate's submitted artifacts, surfaces follow-up questions the panel hasn't covered, and drafts the debrief notes. She listens better instead of typing notes. *Today: she takes notes, misses signals, the debrief gets written from memory two days later.*

---

## Crisis and incidents

**The P0 multi-service incident.** A P0 fires. Multiple service-7 dependencies start failing across the org. The affected troops' chiefs auto-form an incident room (in Slack, with all relevant humans pulled in), coordinate root-cause investigation in parallel, surface findings to the on-call commander, and execute the rollback once approved. The incident is contained in 15 minutes; the postmortem is half-written by the time the incident is closed. *Today: 90 minutes of frantic Slack threads, three duplicate investigations, postmortem drafted next week.*

**The cross-functional security incident.** A potential security breach is detected. The engineering troops affected mobilize containment; the legal troop drafts disclosure scaffolding; the comms troop preps customer notification language; the executive's chief pulls the right people into a room. By the time the CEO is briefed, the full picture and a draft response are ready. *Today: chaos, parallel rediscovery, customers find out before the exec does.*

**The vendor outage cascade.** A critical third-party API starts failing. Every troop that depends on it surfaces the impact immediately; the platform troop drafts a failover plan; the customer success troop preps proactive customer comms. The org reacts coherently within minutes. *Today: each team rediscovers the outage independently and reacts on its own clock.*

---

## Knowledge and cartograph compounding

**The pattern detected across the org.** Three different teams have hit the same race condition in their async job processing over the last month. The cartograph notices the pattern; the Platform team's chief flags it to the platform lead with concrete examples. A single fix at the framework level prevents future occurrences. *Today: each team independently rediscovers the same bug.*

**The pre-warned pitfall.** A new engineer starts on a service. Their chief flags upfront: *"two engineers before you tried approach X and hit problem Y. They eventually solved it with approach Z. Worth knowing before you start."* *Today: the engineer tries X, hits Y, asks around in #help, gets pointed to the person who solved it (if that person still works there).*

**The architecture decision lookup.** A team is debating whether to introduce a new caching layer. Their chief surfaces three past decisions in the cartograph where similar caching was added, what worked, what didn't, and which engineer would be the right consult. The team makes a faster, better-informed decision. *Today: someone asks in #architecture, nobody remembers, the team relitigates the same trade-offs.*

**The dependency-change impact.** An engineer is about to deprecate an internal API. Their chief queries the cartograph and surfaces every troop in the org that has used it in the last 90 days — including non-engineering troops that depend on it via MCPs. The deprecation plan goes out with concrete impact, not "let us know if this affects you." *Today: deprecation hits production, four teams panic, rollback ensues.*

**The proactive runbook contribution.** Diego resolves a tricky service-7 issue at 3am. His chief, after the dust settles, drafts a runbook entry summarizing the failure mode and the fix, asks Diego for two minutes of review, and adds it to the cartograph. The next on-call engineer who hits the same symptom finds the runbook waiting for them. *Today: Diego goes back to sleep, the knowledge dies with him, the next on-call rediscovers from scratch.*

---

## How to use this document

When pitching:

- **To an IC champion (staff/principal engineer):** *3am page, MCP nudge, departure with continuity, appraisal evidence pack, IC override.*
- **To a manager or director:** *Monday standup, 1:1 prep, performance calibration, cross-team friction, skip-level safety.*
- **To a VP or exec:** *Roadmap proposal, skip-level visibility, exec review, finance forecast, the bigger-vision cross-functional stories.*
- **To an indie developer or solo founder:** *Solo founder, pre-revenue indie hacker, weekend WhatsApp, voice ping in the car.*
- **To a platform / DevEx / SRE buyer:** *MCP nudge, P0 multi-service incident, pattern detection, dependency-change impact, proactive runbook.*
- **To skeptics asking "is this just a chatbot?":** *Voice ping in the car, weekend WhatsApp, standup attendee, exec review.*
- **To skeptics asking "is this surveillance?":** *IC override, skip-level visibility, skip-level safety, appraisal evidence pack.*

Update this document whenever a new product capability lands or a pitch conversation surfaces a story worth keeping.
