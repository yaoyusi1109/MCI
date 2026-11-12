# MCI Ambulance Scheduling: Minimize Patient Waiting Time

这是基于 reference #12 的简化扩展模型，按课堂 supply-chain 例子的代码结构实现。

**Scope:** optimize ambulance assignment, trip sequence and hospital choice. Travel times are given inputs; road-edge paths and hospital treatment queues are not decision variables.

**Objective:** minimize total waiting **at the scene**, from readiness after triage to pickup/loading start. This differs from the paper's treatment-completion objectives.

所有输入都是 synthetic example，尚未连接 Baltimore dashboard。所有患者在时间 0 已完成 triage；每辆救护车每趟只运送一人，可多趟往返。医院容量是整个实验期间可接收的总人数，不是可重复使用的床位。

## Sets and parameters

| Symbol | Code | Meaning |
|---|---|---|
| $p\in P$ | `P` | Patients |
| $k\in K$ | `K` | Ambulances |
| $h\in H$ | `H` | Hospitals |
| $q\in Q=\{0,\ldots,|P|-1\}$ | `Q` | Ordered trip positions for each ambulance |
| $s_p$ | `patient_site[p]` | Patient location |
| $\kappa_p$ | `priority[p]` | Triage priority; higher value = higher priority |
| $r_p$ | `ready[p]` | Patient readiness time; zero in this example |
| $e_k$ | `available[k]` | Ambulance availability time |
| $d^0_{ks}$ | `start_time[k][s]` | Initial-location-to-site travel time |
| $d_{sh}$ | `travel_time[s][h]` | Site-to-hospital travel time |
| $\bar d_{hs}$ | `return_time[h][s]` | Hospital-to-next-site travel time |
| $\ell_p$ | `loading[p]` | Patient loading time |
| $v_h$ | `handover[h]` | Hospital unloading/handover time |
| $c_h$ | `hospital_capacity[h]` | Total admission capacity |
| $g_{ph}\in\{0,1\}$ | `compatible[p][h]` | Hospital eligibility |
| $\omega_p>0$ | `weight[p]` | Objective weight; all equal to one by default |

All times are in minutes after time 0. A conservative upper bound $L$ (`horizon`) bounds every timestamp. $M=L+\max_{h,s}\bar d_{hs}$ deactivates unused timing links. The input times are assumed finite and nonnegative; unreachable road pairs must be removed/forbidden in a real-data extension.

## Decision variables

| Variable | Type | Meaning |
|---|---|---|
| $x_{pkqh}$ | Binary | Ambulance $k$ transports patient $p$ to hospital $h$ on trip $q$ |
| $u_{kq}$ | Binary | Trip $q$ of ambulance $k$ is used |
| $a_{kq}$ | Continuous, $[0,L]$ | Pickup/loading start at site |
| $b_{kq}$ | Continuous, $[0,L]$ | Hospital arrival |
| $f_{kq}$ | Continuous, $[0,L]$ | Ambulance free after handover |
| $t_p$ | Continuous, $[0,L]$ | Patient pickup/loading start |
| $w_p$ | Continuous, $[0,L]$ | Patient scene waiting time |

For concise notation only, define expressions $X_{pkq}=\sum_hx_{pkqh}$ and $Y_{hkq}=\sum_px_{pkqh}$. These are sums of existing variables, not additional decision variables.

## Objective function

$$\min\ \sum_{p\in P}\omega_p w_p.$$

With $\omega_p=1$, this minimizes total scene waiting; with a fixed number of patients it also minimizes average scene waiting. Triage is enforced by a separate hard constraint, not merely by objective weights.

## Constraints

**C1. Every patient is served exactly once.**
$$\sum_{k,q,h}x_{pkqh}=1\qquad \forall p.$$

**C2. One patient/hospital assignment per used trip.**
$$\sum_{p,h}x_{pkqh}=u_{kq}\qquad \forall k,q.$$

**C3. Trips are used consecutively.**
$$u_{k,q+1}\le u_{kq}\qquad \forall k,\ q<|Q|-1.$$

**C4. Hospital compatibility.**
$$x_{pkqh}\le g_{ph}\qquad \forall p,k,q,h.$$

**C5. Hospital admission capacity.**
$$\sum_{p,k,q}x_{pkqh}\le c_h\qquad \forall h.$$

**C6. Inactive trip timestamps are zero.**
$$0\le a_{kq},b_{kq},f_{kq}\le L u_{kq}\qquad \forall k,q.$$

**C7. First-trip timing includes initial travel.**
$$a_{k0}\ge e_ku_{k0}+\sum_{p,h}d^0_{k,s_p}x_{pk0h}\qquad \forall k.$$

**C8. Hospital arrival and ambulance release.**
$$b_{kq}=a_{kq}+\sum_{p,h}(\ell_p+d_{s_ph})x_{pkqh}\qquad \forall k,q,$$
$$f_{kq}=b_{kq}+\sum_{p,h}v_hx_{pkqh}\qquad \forall k,q.$$

**C9. A vehicle cannot perform overlapping trips.** It must complete handover and travel from the previous hospital to the next pickup site.
$$a_{k,q+1}\ge f_{kq}+\bar d_{h,s_p}-M(2-Y_{hkq}-X_{p,k,q+1})$$
$$\forall k,\ q<|Q|-1,\ h,p.$$

Only when the previous destination is $h$ AND the next patient is $p$ are both sums one, so the timing constraint is active. The return leg can go to a different site.

**C10. Link each patient's pickup time to its selected trip.**
$$a_{kq}-M(1-X_{pkq})\le t_p\le a_{kq}+M(1-X_{pkq})\qquad\forall p,k,q.$$

**C11. Readiness and waiting definition.**
$$t_p\ge r_p,\qquad w_p=t_p-r_p\qquad\forall p.$$

Waiting ends at loading start. Loading, transport and hospital queues are not counted in $w_p$.

**C12. Triage priority at the same site.**
$$t_p\le t_i\qquad\forall p,i:\ s_p=s_i,\ \kappa_p>\kappa_i.$$

This follows the same-site priority logic of reference #12, constraint (25). Because all patients are ready at time zero, ordering pickup times also orders scene waits. Different sites have no global precedence; equal-priority patients can be reordered; simultaneous pickups are allowed. **Do not apply this rule unchanged to dynamic or staggered patient arrivals.**

**Domains:** $x,u\in\{0,1\}$ and $a,b,f,t,w\in[0,L]$.


Sources: [Reference #12](../reference/12_Repoussis_Emergency_Preparedness_Published.pdf), §3 and constraint (25). See [model README](README.md) for assumptions, execution and validation.
