---
title: "Optimizing Patient Routing in Disaster Scenarios for MCI Frameworks"
subtitle: "EN.560.450/EN.560.650 Civil & Systems Engineering/Operations Research · Instructor: Kimia Ghobadi"
author: "Brianna Monti, Xuzhe Zhi, Yusi Yao, Andzani Mthenjane"
date: "December 9, 2026"
geometry: margin=1in
mainfont: "Times New Roman"
fontsize: 11pt
colorlinks: true
---

# Scope

We are building a dynamic mixed-integer optimization model that decides which ambulance carries which casualty to which hospital after a blast event at Oriole Park at Camden Yards in Baltimore. It is a decision-support and training tool for a slow-moving mass casualty incident (MCI) and a virtual Emergency Medical Resource Center (EMRC), in which Maryland EMS planners and trainers can test resource decisions without staging a full-scale exercise. Casualties are tagged with START (Simple Triage and Rapid Treatment), the triage method designated by the Maryland Medical Protocols for MCIs (MIEMSS, 2008): red/immediate, yellow/delayed, green/minor and black/expectant. Casualties appear over time as they are triaged. A limited ambulance fleet must make repeated trips from the casualty collection point (CCP) to nearby hospitals whose trauma designation and free capacity differ. The model re-plans as the incident evolves: new casualties are triaged, patients deteriorate while they wait, hospital capacity fills, and roads near the stadium are restricted.

**Assumptions and context**

* Patients do not move themselves; they leave the scene only by ambulance. Self-presenting minor patients are not routed and enter the model only as a reduction in hospital intake capacity $c_h$.
* Each ambulance carries one patient, and every transport is treated as emergent.
* Triage is assumed to take about 45 seconds per patient, and an ambulance is assumed to need 60 minutes at the hospital to offload, clean and restock before it returns. Both are modelling assumptions; START itself does not prescribe a time per patient.
* Baltimore's EMS service is based within the fire department. Police officers who arrive on scene are assumed not to be EMS trained; firefighters are, and can contribute to incident command and simple triage.
* No public source gives real-time ambulance counts. Fleet size is an explicit assumption we vary in sensitivity runs.

**Out of scope**

* The model does not represent tagging error: the tag assigned at the scene is assumed to be correct.
* Air transport, paediatric patients (who would be triaged with JumpSTART), pre-event planning and asset pre-positioning, and in-hospital operations beyond a bed and a treatment start time are excluded.
* Patients are assumed only to deteriorate, so improvement from on-scene interventions such as CPR is not modelled.

These are natural directions for future work. Future work could also allow several triaged patients in one ambulance (for example two green and one yellow), several casualty collection points, and hospital capacity that changes over time, with some beds becoming unavailable while others open up. Pre-positioning assets for stadium events is another direction, for example through the existing partnership between MedStar Health and the Baltimore Ravens (<https://www.medstarhealth.org/partnerships/ravens>). Because Wilson et al. (2016) show that errors in estimated task durations substantially reduce the value of an online model, testing our plans against errors in loading, turnover and triage times is a priority. Finally, the model cannot make an optimised decision for a patient until that patient is triaged, so speeding up triage is a goal in its own right.

# Data

While our project works extensively with uncertainty, we use the following data to make educated assumptions.

## OpenStreetMap and Valhalla

Road geometry comes from OpenStreetMap (OpenStreetMap contributors, 2026), and travel times are computed with the open-source Valhalla routing engine, version 3.9.1 (Valhalla contributors, 2026), on a Maryland OpenStreetMap extract. From these we obtain the travel time from each station to the stadium's casualty collection point and from the casualty collection point to each hospital. We also take the coordinates of the stadium access points and of each candidate hospital. The routing model Valhalla solves is given in Appendix C.

## Branas, Sing and Perron (2000)

This three-year geographic case series of MCIs in Maryland anchors our casualty numbers. It reports eight MCIs with 203 injuries in total, an average of 25.4 ± 10.7 injuries per incident (ranging from 10 to nearly 40), of which 77.8% needed ambulance transport, with 3.1 ± 1.1 hospitals involved per incident. We use these figures to size the small and medium scenarios and as a check on the share of patients who need transport. The paper does not report a START mix per incident, so the triage mix is generated at random. A blast in a stadium of 44,970 seats could exceed the largest incident in the series, which is what our large scenario represents.

## EMRC/SYSCOM and Maryland regulations

Oriole Park at Camden Yards seats 44,970 people (MLB, n.d.). The generated casualties become the patient set and their tags, with triage times spread over the incident. The decision channels and stakeholders the model is meant to support come from the [MIEMSS Emergency Medical Resource Center (EMRC)](https://www.miemss.org/home/departments/emrc), Maryland's statewide communications system linking ambulances, dispatch centers, hospital emergency departments and trauma centers.

As a benchmark for on-site medical staffing at a large crowd we use [COMAR 10.16.05.06B(3)](https://regs.maryland.gov/us/md/exec/comar/10.16.05.06). This regulation sets health-permit conditions for outdoor musical festivals; it excludes festivals in Baltimore City and events in permanent structures or on publicly owned property, so it does not apply to a game at Camden Yards. We cite it only as the State's stated minimum for an event of more than 10,000 people, where emergency medical services must be on site from the arrival of the first spectators until their departure, with a minimum of:

1. Four first responders who are:
   1. Licensed or certified in Maryland in accordance with Education Article, §13-516, Annotated Code of Maryland;
   2. Trained in CPR and the use of automatic defibrillators; and
   3. Functioning under an emergency medical services operational program as defined in COMAR 30.02.01.01B;
2. Two advanced life support providers who are:
   1. Licensed or certified in Maryland in accordance with Education Article, §13-516, Annotated Code of Maryland; and
   2. Functioning under an emergency medical services operational program as defined in COMAR 30.02.01.01B;
3. One physician licensed in the State; and
4. One advanced life support ambulance and crew functioning under an emergency medical services operational program as defined in COMAR 30.02.01.01B.

## Hospitals

Hospital locations, licensed bed counts and reported trauma level come from the Homeland Infrastructure Foundation-Level Data ([HIFLD](https://portal.datarescueproject.org/datasets/hifld-open-hospitals/)) Open Hospitals layer, a Department of Homeland Security dataset. HIFLD Open was taken offline in August 2025, so we use the copy archived by the Data Rescue Project. We filter it to Maryland and keep the hospitals within reach of the stadium, using the name, address, coordinates, hospital type, operational status, bed count and trauma level.

The HIFLD trauma field is free text and can be out of date, so trauma designations are checked against Maryland's designated trauma centers (Maryland TraumaNet, n.d.). The R Adams Cowley Shock Trauma Center is Maryland's Primary Adult Resource Center (PARC), The Johns Hopkins Hospital is a Level I center, and Johns Hopkins Bayview Medical Center and Sinai Hospital of Baltimore are Level II centers. These designations set the compatibility indicator $g_{ph}$: red patients go only to designated adult trauma centers (PARC, Level I or Level II). A licensed bed count is not the number of MCI patients a hospital can take, so the intake capacity $c_h$ is an explicit assumption.

None of these sources gives the number of ambulances, so fleet size is a stated assumption that we also vary in sensitivity runs.

# Methods

## Sets and parameters

| Symbol | Code | Meaning |
| :---------- | :---------------- | :------------------------------------------------ |
| $p\in P$ | P | Patients |
| $P_\tau \subseteq P$ | | Patients triaged, waiting and needing transport at decision time $\tau$ |
| $P_\tau^{c}$ | | Patients in $P_\tau$ with observed tag $c\in\{\text{RED},\text{YELLOW},\text{GREEN}\}$ |
| $k\in K$ | K | Ambulances |
| $h\in H$ | H | Hospitals |
| $s\in S$ | S | Casualty collection points (sites) |
| $q\in Q$ | Q | Ordered trip positions for each ambulance, $Q=\{0,\ldots,\vert P\vert-1\}$ |

| Symbol | Code | Meaning |
| :---------- | :---------------- | :------------------------------------------------ |
| $s_p$ | patient\_site\[p\] | Site of patient $p$ |
| $\kappa_p$ | priority\[p\] | Triage priority; higher value = higher priority |
| $r_p$ | ready\[p\] | Time patient $p$ is triaged and ready for transport |
| $e_k$ | available\[k\] | Availability time of ambulance $k$ |
| $d^{0}_{ks}$ | start\_time\[k\]\[s\] | Travel time from ambulance $k$'s initial location to site $s$ |
| $d_{sh}$ | travel\_time\[s\]\[h\] | Travel time from site $s$ to hospital $h$ |
| $\bar d_{hs}$ | return\_time\[h\]\[s\] | Travel time from hospital $h$ back to site $s$ |
| $\ell_p$ | loading\[p\] | Loading time of patient $p$ |
| $v_h$ | turnover\[h\] | Turnover at hospital $h$: offloading, cleaning and restocking |
| $c_h$ | hospital\_capacity\[h\] | Assumed MCI intake capacity of hospital $h$ |
| $g_{ph}\in\{0,1\}$ | compatible\[p\]\[h\] | 1 if hospital $h$ may receive patient $p$ |
| $\tau$ | current\_time | Current decision time (minutes since the start of the incident) |
| $L$ | horizon | Planning horizon; upper bound on every time variable |
| $M$ | | Big-M constant, $M = L + \max_{h,s}\bar d_{hs}$ |

## Decision variables

| Variable | Type | Meaning |
| :---------- | :---------------- | :------------------------------------------------ |
| $x_{pkqh}$ | Binary | 1 if ambulance $k$ transports patient $p$ to hospital $h$ on trip $q$ |
| $u_{kq}$ | Binary | 1 if trip $q$ of ambulance $k$ is used |
| $z_p$ | Binary | 1 if patient $p$ is transported in the plan |
| $a_{kq}$ | Continuous, $[0,L]$ | Time pickup/loading starts at the site on trip $q$ of ambulance $k$ |
| $b_{kq}$ | Continuous, $[0,L]$ | Hospital arrival time on trip $q$ of ambulance $k$ |
| $f_{kq}$ | Continuous, $[0,L]$ | Time ambulance $k$ is free after turnover on trip $q$ |
| $t_p$ | Continuous, $[0,L]$ | Pickup/loading start time of patient $p$ |
| $w_p$ | Continuous, $[0,L]$ | Scene waiting time of patient $p$ (reported, not optimised) |
| $C$ | Continuous, $[0,L]$ | Patient-delivery makespan: the latest hospital arrival |

## Objective

The objective is lexicographic (Gurobi multi-objective, priorities 4 > 3 > 2 > 1): first transport as many red patients as possible, then yellow, then green, and only then finish as early as possible.

$$(1)\quad \max \sum_{p\in P_\tau^{\text{RED}}} z_p$$

$$(2)\quad \max \sum_{p\in P_\tau^{\text{YELLOW}}} z_p$$

$$(3)\quad \max \sum_{p\in P_\tau^{\text{GREEN}}} z_p$$

$$(4)\quad \min\ C$$

$C$ is the latest hospital arrival of any transported patient (constraint C13). This differs from Repoussis et al. (2016), whose makespan is the latest completion of treatment at the hospitals.

## Constraints

The constraints are listed in Appendix A.

## Dynamic re-planning

The formulation above is a full-horizon plan. In the dynamic simulation the clock advances in 45-second steps (0.75 min), and the problem is re-solved whenever an ambulance is free at the casualty collection point. At each re-solve:

* $P_\tau$ contains only patients who are triaged, waiting and need transport. Trips already under way, beds already reserved and patients already delivered are fixed. Because every patient in $P_\tau$ is ready at $\tau$, the same-site priority rule C12 remains valid.
* The model chooses the next dispatch only: one patient and one hospital for the free ambulance. Its priorities are, in order, the highest observed triage tag, the longest wait since triage, and the shortest drive to a compatible hospital with a free bed.
* Patient condition evolves between re-solves. Each patient $p$ has a deterioration interval $\delta_p$ (minutes per triage level; 30, 45 and 90 minutes for the red, yellow and green patients of the base scenario). After $\delta_p$ minutes of waiting, the true tag moves one level (green to yellow, yellow to red, red to black), and the rate halves while a responder is stabilising the patient. The model sees a change only when the patient is re-assessed, every 15 minutes. A patient observed as black is not transported (expectant).
* Hospital beds are tracked in a ledger: a bed is reserved at dispatch and occupied on arrival, and it is not released during the incident.
* Each ambulance cycle is loading (5 minutes), the drive to the hospital, turnover (60 minutes) and the drive back to the casualty collection point.

This rolling, single-dispatch use of the model is a heuristic and is not optimal over the whole horizon. Shiri, Akbari and Salman (2024) show that on a worst-case instance no online algorithm can attain a finite competitive ratio, and Mills, Argon and Ziya (2018) find that a myopic policy that decides only for the next patient already increases the expected number of survivors over transporting to the nearest facility.

# References

Branas, C.C., Sing, R.F. and Perron, A.D. (2000). 'A case series analysis of mass casualty incidents', *Prehospital Emergency Care*, 4(4), pp. 299–304. doi:10.1080/10903120090940985.

Chang, K.-H., Chen, T.-L., Yu, L.-H. and Chang, T.-Y. (2026). 'Simulation-based dynamic optimization for ambulance dispatch and casualty distribution in mass casualty incident', *Computers & Operations Research*, 194, 107559. doi:10.1016/j.cor.2026.107559.

Homeland Infrastructure Foundation-Level Data (HIFLD) (2025). *HIFLD OPEN Hospitals*. Department of Homeland Security; archived by the Data Rescue Project. Available at: <https://portal.datarescueproject.org/datasets/hifld-open-hospitals/>.

Levy, M. and Taber, W.F. (2026). Interview with Matt Levy, professor of emergency medicine, deputy director of EMS and special operations at Johns Hopkins, and Howard County and regional medical director for Greater Baltimore, and with William Forrester Taber, senior tactical fellow at Johns Hopkins focusing on special operations and pre-hospital medicine for Maryland and federal operations. Personal communication, 5 November 2026.

Maryland Division of State Documents (n.d.). *Code of Maryland Regulations (COMAR) 10.16.05.06, Health Permits for Outdoor Musical Festivals: Emergency Medical Services*. Available at: <https://regs.maryland.gov/us/md/exec/comar/10.16.05.06>.

Maryland Institute for Emergency Medical Services Systems (MIEMSS) (n.d.). *Emergency Medical Resource Center (EMRC)*. Available at: <https://www.miemss.org/home/departments/emrc>.

Maryland Institute for Emergency Medical Services Systems (MIEMSS) (2008). *Maryland Medical Protocols for Emergency Medical Services Providers*. Available at: <https://history.miemss.org/documents/2008_Protocol_2008.pdf>.

Maryland TraumaNet (n.d.). *Trauma Centers in Maryland*. Available at: <https://www.maryland-traumanet.com/resources/trauma-centers/>.

Major League Baseball (MLB) (n.d.). *Camden Yards guide: capacity, seating chart, parking and more*. Available at: <https://www.mlb.com/news/camden-yards-guide-capacity-seating-chart-parking-and-more>.

Mills, A.F., Argon, N.T. and Ziya, S. (2018). 'Dynamic distribution of patients to medical facilities in the aftermath of a disaster', *Operations Research*, 66(3), pp. 716–732. doi:10.1287/opre.2017.1695.

OpenStreetMap contributors (2026). *OpenStreetMap* [dataset]. OpenStreetMap Foundation. Maryland extract accessed 11 November 2026. Available at: <https://www.openstreetmap.org/>.

Repoussis, P.P., Paraskevopoulos, D.C., Vazacopoulos, A. and Hupert, N. (2016). 'Optimizing emergency preparedness and resource utilization in mass-casualty incidents', *European Journal of Operational Research*, 255, pp. 531–544. doi:10.1016/j.ejor.2016.05.047.

Salmerón, J. and Apte, A. (2010). 'Stochastic optimization for natural disaster asset prepositioning', *Production and Operations Management*, 19(5), pp. 561–574. doi:10.3401/poms.1080.01119.

Shiri, D., Akbari, V. and Salman, F.S. (2024). 'Online algorithms for ambulance routing in disaster response with time-varying victim conditions', *OR Spectrum*, 46, pp. 785–819. doi:10.1007/s00291-024-00744-4.

Valhalla contributors (2026). *Valhalla*, version 3.9.1 [software]. Available at: <https://github.com/valhalla/valhalla>.

Wilson, D.T., Hawe, G.I., Coates, G. and Crouch, R.S. (2016). 'Online optimization of casualty processing in major incident response: an experimental analysis', *European Journal of Operational Research*, 252(1), pp. 334–348. doi:10.1016/j.ejor.2016.01.021.

# Appendix A: Constraints

**C1. Each patient is transported at most once.** $z_p$ records whether patient $p$ is transported, so the lexicographic objective can leave a patient unserved when beds or ambulances run out.

$$\sum_{k\in K}\sum_{q\in Q}\sum_{h\in H} x_{pkqh} = z_p \le 1 \qquad \forall\, p\in P_\tau$$

**C2. One patient/hospital assignment per used trip.**

$$\sum_{p\in P_\tau}\sum_{h\in H} x_{pkqh} = u_{kq} \qquad \forall\, k\in K,\ q\in Q$$

**C3. Trips are used consecutively.**

$$u_{k,q+1} \le u_{kq} \qquad \forall\, k\in K,\ q\in Q,\ q<\vert Q\vert-1$$

**C4. Hospital compatibility.**

$$x_{pkqh} \le g_{ph} \qquad \forall\, p\in P_\tau,\ k\in K,\ q\in Q,\ h\in H$$

**C5. Hospital intake capacity.**

$$\sum_{p\in P_\tau}\sum_{k\in K}\sum_{q\in Q} x_{pkqh} \le c_h \qquad \forall\, h\in H$$

**C6. Timestamps of inactive trips are zero.**

$$0 \le a_{kq},\ b_{kq},\ f_{kq} \le L\, u_{kq} \qquad \forall\, k\in K,\ q\in Q$$

**C7. First-trip timing includes initial travel.**

$$a_{k0} \ge e_k\, u_{k0} + \sum_{p\in P_\tau}\sum_{h\in H} d^{0}_{k,s_p}\, x_{pk0h} \qquad \forall\, k\in K$$

**C8. Hospital arrival and ambulance release.**

$$b_{kq} = a_{kq} + \sum_{p\in P_\tau}\sum_{h\in H} \left(\ell_p + d_{s_p h}\right) x_{pkqh} \qquad \forall\, k\in K,\ q\in Q$$

$$f_{kq} = b_{kq} + \sum_{p\in P_\tau}\sum_{h\in H} v_h\, x_{pkqh} \qquad \forall\, k\in K,\ q\in Q$$

**C9. An ambulance cannot perform overlapping trips.** It must complete turnover and travel from the previous hospital to the next pickup site. For brevity let $X_{pkq}=\sum_{h\in H} x_{pkqh}$ and $Y_{hkq}=\sum_{p\in P_\tau} x_{pkqh}$; these are sums of existing variables, not new variables.

$$a_{k,q+1} \ge f_{kq} + \bar d_{h,s_p} - M\left(2 - Y_{hkq} - X_{p,k,q+1}\right) \qquad \forall\, k\in K,\ q<\vert Q\vert-1,\ h\in H,\ p\in P_\tau$$

Only when the previous destination is $h$ and the next patient is $p$ are both sums equal to one, so the timing constraint is active. The return leg can go to a different site.

**C10. Link each patient's pickup time to the selected trip.**

$$a_{kq} - M\left(1 - X_{pkq}\right) \le t_p \le a_{kq} + M\left(1 - X_{pkq}\right) \qquad \forall\, p\in P_\tau,\ k\in K,\ q\in Q$$

**C11. Readiness and waiting time.** For transported patients, waiting ends when loading starts; loading, transport and hospital queues are not counted in $w_p$.

$$t_p \ge r_p, \qquad w_p = t_p - r_p \qquad \forall\, p\in P_\tau$$

**C12. Triage priority at the same site.**

$$t_p \le t_i \qquad \forall\, p,i\in P_\tau:\ s_p=s_i,\ \kappa_p>\kappa_i$$

This follows the same-site priority logic of Repoussis et al. (2016), constraint (25). Different sites have no global precedence, equal-priority patients can be reordered, and simultaneous pickups are allowed. In the dynamic setting the rule is applied within each re-solve, where every patient in $P_\tau$ is already ready at $\tau$.

**C13. Patient-delivery makespan.**

$$C \ge b_{kq} \qquad \forall\, k\in K,\ q\in Q$$

**Variable domains.**

$$x_{pkqh},\ u_{kq},\ z_p \in \{0,1\}; \qquad a_{kq},\ b_{kq},\ f_{kq},\ t_p,\ w_p,\ C \in [0,L]$$

**Source.** Constraints C2 to C12 adapt Repoussis et al. (2016), Section 3 and constraint (25). C1 (relaxed to "at most once" with $z_p$) and C13 are our additions for the lexicographic objective.

# Appendix B: Literature

## 1. [Repoussis et al. (2016)](https://openaccess.city.ac.uk/id/eprint/19862/)

Our model is a time-sliced, deterioration-aware extension of Repoussis et al. (2016). The other sources supply the pieces Repoussis leaves out.

This source provides the structural basis of our model. Repoussis et al. formulate the response to a mass casualty incident as a mixed-integer program that determines ambulance dispatching, patient-to-hospital assignment and the order of treatment at each hospital. The problem is treated as a flexible job shop in which patients are jobs, ambulances and hospital beds are the machines, and travel times act as sequence-dependent setup times. The model follows START triage in a priority order, considers hospital trauma level and capacity, and minimises the makespan, defined as the latest completion time of treatment over all patients ($C_{max}$). The authors show that exact solution becomes difficult as patients are added and propose a MIP-based construction heuristic with local search, with which they examine ambulance and hospital-bed bottlenecks. Our adaptation differs in that it is dynamic rather than static: we re-solve as the incident evolves, with trips already under way fixed and newly triaged casualties added, which is the direction taken by Shiri, Akbari and Salman (2024). Unlike this source, we also let patient condition deteriorate over time instead of keeping a fixed triage level: a patient's tag rises when the waiting time passes a deadline. Casualty counts and mix, blocked roads and hospital availability are represented as a small set of weighted scenarios, following Salmerón and Apte (2010).

## 2. [Shiri, Akbari and Salman (2024)](https://eprints.whiterose.ac.uk/id/eprint/216001/)

This source studies ambulance routing after a disaster when key information is uncertain and is learned gradually while ambulances are already en route. The uncertain quantities are travel times, victims' triage levels, treatment times and changes in victims' conditions. The authors evaluate online algorithms by their competitive ratio against an offline solution with full information, show that on a worst-case instance neither deterministic nor randomized online algorithms can attain a finite competitive ratio, and develop several heuristics that run in very low computation time. For our project it supports the step from a static to a dynamic model: re-planning as information arrives, with fast heuristics as a fallback when the exact model is too slow. We take the idea that information arrives over time and build it into an optimization model that is re-solved as the incident evolves, using their treatment of changing victim condition to set the deadlines at which yellow patients become red and red patients are lost.

## 3. [Salmerón and Apte (2010)](https://faculty.nps.edu/jsalmero/docs/2010_POM.pdf)

This source develops a two-stage stochastic optimization model for positioning relief assets before a natural disaster. First-stage decisions allocate a budget to expand resources, while second-stage decisions cover the logistics of evacuating the critical population, delivering commodities to the population that stays and transporting the displaced population. The objective is to minimise the expected number of casualties, and uncertainty about the event's location and severity is represented by scenarios with probabilities. The model works with population groups rather than individual patients, so it does not route patients during an event, but it gives us a template for scenario-based uncertainty and probability-weighted objectives, which we could use for road restrictions, hospital availability and deterioration times.

## 4. [Wilson et al. (2016)](https://www.sciencedirect.com/science/article/pii/S0377221716000503)

The authors extend their earlier multi-objective combinatorial model of casualty processing so that it can run in real time with continuous communication with the incident environment. Casualty numbers change over time and task-duration estimates contain errors, and the authors test these effects in simulation experiments. They find that moving to an online system helps offset poor communication speed, while errors in estimating task durations substantially reduce the model's usefulness. We do not use their model or software, and our current runs do not yet perturb task durations. Their finding is the reason we list testing against errors in loading, turnover and triage times as a priority for future work.

## 5. [Chang, Chen, Yu and Chang (2026)](https://www.sciencedirect.com/science/article/pii/S0305054826001772)

This source studies the sudden influx of casualties from several zones after an earthquake, when ambulances and hospital beds are overwhelmed and conditions change continuously. The authors formulate a Markov decision process that makes real-time decisions on ambulance dispatch, ambulance redeployment and hospital selection from the current state of the system, with the aim of maximising the expected survival rate, and solve it with simulation-based approximate dynamic programming. This is the closest recent work to our dynamic setting. We adapt its view of the incident as a system whose state evolves and is re-assessed over time, and its comparison against current-practice rules such as nearest-hospital-first, which our nearest-hospital baseline mirrors. We differ in method and setting: instead of learning a policy by simulation, we solve an explicit mixed-integer program at each re-planning point, so that every recommendation can be traced to a constraint and inspected by trainers, and we model a single-site blast with road restrictions rather than an earthquake across several zones.

## 6. [Mills, Argon and Ziya (2018)](https://doi.org/10.1287/opre.2017.1695)

This source studies where to send patients after a disaster when transport is limited. Using a Markov decision process formulation, the authors develop heuristic policies that decide how to allocate ambulances to patient locations and which medical facility each ambulance should go to, using limited information such as mean travel times and congestion levels. In a simulation study with injury-specific survival rates and service times, the heuristics substantially increase the expected number of survivors compared with the common practice of transporting to the nearest facility, and a myopic policy that considers only the next patient to be transported already improves on it in almost all scenarios. This supports our rolling, single-dispatch use of the model.

# Appendix C: Routing Model (Valhalla)

Road geometry comes from OpenStreetMap, and routes are computed with the open-source Valhalla routing engine (version 3.9.1) on a Maryland OpenStreetMap extract. The road network is a directed graph $G=(V,E)$ whose nodes $V$ are intersections and whose arcs $E$ are road segments; one-way streets give a single arc and two-way streets give two.

| Symbol | Meaning |
| :---------- | :------------------------------------------------------------------ |
| $o,\ o'\in V$ | Origin and destination of a trip (ambulance start location, site or hospital) |
| $t_{ij}$ | Driving time on road segment $(i,j)\in E$ |
| $\gamma_{ij}$ | Valhalla cost of segment $(i,j)$: $t_{ij}$ plus penalties for turns, traffic signals and minor roads ("auto" costing) |
| $y_{ij}\in\{0,1\}$ | 1 if the route uses road segment $(i,j)$ |

For each origin–destination pair, Valhalla finds the least-cost route:

$$\min \sum_{(i,j)\in E} \gamma_{ij}\, y_{ij}$$

$$\sum_{j:(i,j)\in E} y_{ij} - \sum_{j:(j,i)\in E} y_{ji} =
\begin{cases} 1 & i=o \\ -1 & i=o' \\ 0 & \text{otherwise} \end{cases} \qquad \forall\, i\in V$$

$$y_{ij}\in\{0,1\} \qquad \forall\, (i,j)\in E$$

The flow-conservation constraints send one unit of flow from $o$ to $o'$, so the selected arcs form a path. Because the constraint matrix is the node–arc incidence matrix of $G$, it is totally unimodular, and the linear relaxation already has an integer optimum. Valhalla therefore does not call an LP solver; it solves the problem with a bidirectional A\* search over its tiled road graph, which is fast enough to answer every query during the incident. The travel time of the chosen route is
$$T(o,o')=\sum_{(i,j)\in E} t_{ij}\, y^{*}_{ij}.$$
Because $\gamma_{ij}$ includes penalties, this is the time of the least-cost route, which can be slightly longer than the fastest possible route.

The routing model supplies the three travel-time parameters of the main model:

| Symbol | Code | Valhalla query |
| :---------- | :---------------- | :---------------------------------------- |
| $d^{0}_{ks}$ | initial\_route | $T(\text{initial location of } k,\ s)$ |
| $d_{sh}$ | route, travel\_times\[h\] | $T(s,\ h)$ |
| $\bar d_{hs}$ | return\_route | $T(h,\ s)$ |

In the dynamic simulation these travel times are computed once, before the incident clock starts (Appendix D). If Valhalla finds no route from a site to a hospital, that hospital is left out of the assignment for that site.

# Appendix D: Simulation Loop

The simulation links the routing model (Appendix C) and the dispatch model over time. It holds the parameters that describe what happens after a dispatch decision.

| Symbol | Code | Meaning |
| :---------- | :---------------- | :------------------------------------------------ |
| $\ell_p$ | loading\_minutes | Loading time of patient $p$ at the site (5 minutes) |
| $v_h$ | turnover\_minutes | Turnover at hospital $h$: offloading, cleaning and restocking (60 minutes) |
| $d^{0}_{ks}$ | initial\_route | Travel time from ambulance $k$'s initial location to site $s$ (from Valhalla) |
| $d_{sh}$ | route | Travel time from site $s$ to hospital $h$ (from Valhalla) |
| $\bar d_{hs}$ | return\_route | Travel time from hospital $h$ back to site $s$ (from Valhalla) |

1. **Routing.** Before the clock starts, Valhalla computes $d^{0}_{ks}$, and $d_{sh}$ and $\bar d_{hs}$ for every candidate hospital. A hospital with no route is left out.
2. **Arrival.** Ambulance $k$ leaves its initial location at time 0 and is free at site $s$ at time $d^{0}_{ks}$.
3. **Dispatch.** Whenever an ambulance is free at the site, Gurobi chooses its next dispatch: one patient in $P_\tau$ and one compatible hospital with a free bed (see Dynamic re-planning). The bed is reserved at dispatch.
4. **Execution.** The ambulance loads ($\ell_p$) and drives to the hospital, arriving at $\tau+\ell_p+d_{sh}$, where the reserved bed becomes occupied. After turnover ($v_h$) it drives back ($\bar d_{hs}$) and is free at the site again at $\tau+\ell_p+d_{sh}+v_h+\bar d_{hs}$.
5. **Re-solve.** The clock advances in 0.75-minute steps. Newly triaged patients join $P_\tau$ and waiting patients may deteriorate (see Dynamic re-planning). Steps 3–4 repeat until every patient who needs transport has been delivered; the incident makespan is the hospital arrival time of the last of them.
