# OpenUp Queue Model readme
last updated: Feb 8, 2021

---

## I. Contents
This repository contains all the files for to simulate OpenUp service desk operation.

Two models are now available:

The first version `queue_simulation.py` incorporates interrupts to sign in and sign out counsellors.  

In this version, counsellors do not have to work overtime and are allowed to have a meal break during `AM`, `PM`, and `Special` shifts.
This break is currently set to `60 minutes`. During the graveyard shift, the meal break is actually a `180-minute` "nap time".
No more new cases are assigned to counsellors who are `30 minutes` away from the end shift time.  They will be required to finish
up their existing cases until the shift ends, upon which those cases will be forwarded back to the waiting queue.

The use of interrupts at specific intervals mean that Counsellors always take the break and sign out at the given time.
Their clients will be transferred to the waiting queue and wait to be served by another available counsellor, or drop out if the wait time exceeds their patience.  

Special precautions should be taken when changing the variables in the script.  
The spacing of "nap time" is set such that at least a duty officer or a social worker is on duty while the other takes a nap.
In making sure the operation is manned by at least one person, all four-hour volunteer shifts are also spaced 
intervals apart from the paid worker shifts.

The second version `queue_simulation2.py` does not factor in breaks.  Also in this version, counsellors may be required to work overtime.

The uptake of cases depends on the assigned user chat time.  
The counsellor will only serve the user if the case be served under the `remaining shift duration` - `30-minute` cutoff time.
While in reality counsellors cannot predict the chat time, this sentinel is needed to prevent the rare edge case whereby 
counsellors handle marathon cases many minutes (days) beyond their assigned signoff time.

---

## II. Pip requirements
+ a working python virtual environment - follow Python or Conda documentation to set up one if you haven't already done so.
+ `simpy` ~= 4.0
+ `jupyter-core` >=4.5 and associated data science packages if running Jupyter Notebook file
+ `numpy` ~=1.19 and `pandas` ~=1.1

---


## III. Usage
1. Source into the python virtual environment.
2. Either: a) In `jupyter notebook` run the notebook file `queue_simulation.ipynb` to get bootstrapped results, or b) enter `python queue_simulation.py` in bash.

Full details on usage in the main function of `queue_simulation.py` and the Jupyter notebook `queue_simulation.ipynb` 

## IV. Changes
The latest updates now assign chat duration by risklevel rather than by counsellor type as the monthly reports in OpenUp 1.0 in later months
show that the discrepency of the chat duration by risklevel is greatest.  

In preparation for journal submissions, in the code all variables containing the prefix or suffix `helpseeker` are replaced with the term `user`.

Diagram formatting is slightly revised.  Pandas and Numpy libraries are used to speed up multiprocessing during bootstrap.
Interarrivals folder now contains the last three months of interarrival data from Sep 2020 to Nov 2020.  The older version of the interarrivals file,
designed for previous iterations of the `ServiceOperation` Class, has been removed from the repository since February 2021.
