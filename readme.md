# OpenUp Queue Model readme
last updated: Mar 9, 2021

---

## I. Contents
This repository contains all the files for to simulate OpenUp service desk
operation.

The file `queue_simulation.py` incorporates interrupts to sign in and
sign out counsellors.  Counsellors do not have to work overtime and are 
allowed to have a meal break during `AM`, `PM`, and `Special` shifts.
This break is currently set to `60 minutes`. During the graveyard shift,
the meal break is actually a `180-minute` "nap time".
No more new cases are assigned to counsellors who are `30 minutes` away from
the end shift time.  They will be required to finish
up their existing cases until the shift ends, upon which ongoing cases will be
forwarded back to the waiting queue.

Please use the version `queue_simulation_zombies.py` when zombie cases have to
be included in the simulation.

The use of `interrupts` at specific intervals forces Counsellors to always take 
the break and sign out at a prescribed time.  Their clients will be transferred
to the waiting queue and wait to be served by another available counsellor, or 
drop out if the wait time exceeds their patience.  

Special precautions should be taken when changing the variables in the script.  
The spacing of "nap time" is set such that at least a duty officer or a social
worker is on duty while the other takes a nap.  In making sure the operation is 
manned by at least one person during one-hour meal breaks, all four-hour
volunteer shifts are also spaced intervals apart from the paid worker shifts.

The polling version (non-interrupt version `queue_simulation2.py`) has been
phased out and will not be updated in the future.


`queue_interarrival_service_duration_exploration.ipynb` is added to produce
the descriptive statistics, to explore the data distributions of renege time,
chat time, and interarrival time, and to generate the interarrivals file needed
for simulations beyond Nov. 30, 2020.

---

## II. Pip requirements
+ a working python virtual environment - follow Python or Conda documentation
to set up one if you haven't already done so.
+ `simpy` ~= 4.0
+ `jupyter-core` >=4.5 and associated data science packages if running Jupyter 
Notebook file
+ `numpy` ~=1.19 and `pandas` ~=1.1
+ `python` >=3.7
---


## III. Usage
1. Source into the python virtual environment.
2. Run the `jupyter notebook` file 
`queue_interarrival_service_duration_exploration.ipynb` if generation of new
interarrivals is needed. (Optional)
3. Either: a) In `jupyter notebook` run the notebook file 
`queue_simulation.ipynb` to get bootstrapped results, or 
b) enter `python queue_simulation.py` in bash.

Full details on usage in the main function of `queue_simulation.py` and the
Jupyter notebook `queue_simulation.ipynb` 



## IV. Changes
The latest updates now assign chat duration by risklevel rather than by 
counsellor type as the monthly reports in OpenUp 1.0 in later months
show that the discrepency of the chat duration by risklevel is greatest.

In preparation for journal submissions, in the code all variables containing 
the prefix or suffix `helpseeker` are replaced with the term `user`.

Diagram formatting has been revised.  Pandas and Numpy libraries are used 
to speed up multiprocessing during bootstrap.  Interarrivals folder contains
the **actual** interarrival data from Nov 2020, which can be used to simulate
approximate manpower needed to handle users for the paper.

In earlier simulation iterations, interarrivals times were selected from a list of mean
interarrivals, read in from a csv file.  This version has been been phased out
and removed from the repository since March 2021.

With multiprocessing implemented to run the simulation, in 
`queue_simulation.py` the renege time and chat time distributions
have been revised to follow the gamma distribution, allowing simulations
to better emulate actual SO conditions.

`ServiceOperation.assign_interarrival_time()` has been rewritten to allow
sequence of arrivals to be generated using the thinning algorithm.  It 
can take a **long time** to produce results, so when thinning is specified during
simulations, please lower the number of bootstrap iterations to 150 or fewer.

The layouts and diagrams in `queue_interarrival_service_duration_exploration.ipynb`
have been revised.  All diagrams have been updated for the preparation of the
paper.