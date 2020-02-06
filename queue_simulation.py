'''
    This program uses Simpy to simulate the OpenUp Counselling Service 
    platform and helpseeker arrivals.  

    Helpseekers will renege when they loose patience waiting in the queue

    For more details about the Simpy syntax, please visit
    https://simpy.readthedocs.io/en/latest/contents.html

    Discrete Event Simulation Primer:
    https://www.academia.edu/35846791/Discrete_Event_Simulation._It_s_Easy_with_SimPy_
'''

import simpy, random, enum
from simpy.util import start_delayed
from pprint import pprint
# from scipy.stats import poisson



# Globals
MAX_NUM_SIMULTANEOUS_CHATS = 4              # maximum number of simultaneous chats allowed
SEED = 728                                  # for seeding the sudo-random generator
MINUTES_PER_DAY = 24 * 60                   # 1440 minutes per day
SIMULATION_DURATION = MINUTES_PER_DAY * 31  # currently given as num minutes 
                                            # per day * num days in month

################################################################################
# Enums and constants
################################################################################

class Colors:
    '''
        Color codes for terminal
    '''
    GREEN =  '\033[32m'
    RED =  '\033[91m'
    WHITE = '\033[0m'
    BLUE = '\033[94m'

    HGREEN = '\x1b[6;37;42m'
    HRED = '\x1b[6;37;41m'   
    HWHITE = '\x1b[6;37;47m'   
    HBLUE = '\x1b[6;37;44m' 
    HEND = '\x1b[0m'  

#-------------------------------------------------------------------------------

class Shifts(enum.Enum):
    '''
        different types of shifts
        shift start, end, and next shift offset in minutes
    '''

    GRAVEYARD = ('GRAVEYARD',   1290, 1890, 840, 3)#2) # from 9:30pm to 7:30am
    AM =        ('AM',          435, 915, 960, 3) #2)   # from 7:15am to 3:15 pm
    PM =        ('PM',          840, 1320, 960, 3)#2)  # from 2pm to 10pm
    SPECIAL =   ('SPECIAL',     1020, 1500, 960, 2)#1) # from 5pm to 1 am

    def __init__(self, shift_name, start, end, offset, capacity):
        self.shift_name = shift_name
        self.start = start
        self.end = end
        self.offset = offset
        self.capacity = capacity

    @property
    def duration(self):
        return int(self.end - self.start)

    @property
    def lunch_start(self):
        # define lunch as the midpoint of shift
        return self.start + (self.end - self.start) / 2
        
#-------------------------------------------------------------------------------

class JobStates(enum.Enum):
    '''
        Counsellor in three states:
        counselling, eating lunch, and adhoc duties,
        each of which are given different priorities (must be integers)

        The higher the priority, the lower the value 
        (10 has higher priority than 20)
    '''

    SIGNOUT =       ('SIGNED OUT',      10)
    COUNSELLING =   ('COUNSELLING',     20)
    LUNCH =         ('EATING LUNCH',    20)
    AD_HOC =        ('AD HOC DUTIES',   30)

    def __init__(self, job_name, priority):
        self.job_name = job_name
        self.priority = priority

#-------------------------------------------------------------------------------

class AdHocDuty(enum.Enum):
    '''
        different types of shifts
        shift start, end, and next shift offset in minutes
    '''

    MORNING =   ('MORNING', 600, 840)       # from 10am to 2pm
    AFTERNOON = ('AFTERNOON', 840, 1080)    # from 2pm to 6pm
    EVENING =   ('EVENING', 1080, 1320)     # from 6pm to 10pm    

    def __init__(self, period_name, start, end):
        self.period_name = period_name
        self.start = start
        self.end = end

    @property
    def duration(self):
        return self.end - self.start

#-------------------------------------------------------------------------------

class Risklevels(enum.Enum):
    '''
        Distribution of LOW/MEDIUM/HIGH/CRISIS - 82%/16%/1.5%/0.5%
    '''

    CRISIS =    ('CRISIS',  .005)
    HIGH =      ('HIGH',    .015)
    MEDIUM =    ('MEDIUM',  .16)
    LOW =       ('LOW',     .82)

    def __init__(self, risk, probability):
        self.risk = risk
        self.probability = probability

#-------------------------------------------------------------------------------

class Users(enum.Enum):
    '''
        Distribution of Repeated Users - 75% regular / 25% repeated
    '''

    REPEATED =  ('REPEATED USER',   .25) 
    REGULAR =   ('REGULAR USER',    .75) 
    
    def __init__(self, user_type, probability):
        self.user_type = user_type
        self.probability = probability

#-------------------------------------------------------------------------------

class Roles(enum.Enum):
    '''
        Counsellor Roles
    '''

    SOCIAL_WORKER = ('SOCIAL WORKER',   4)
    DUTY_OFFICER =  ('DUTY OFFICER',    2)
    VOLUNTEER =     ('VOLUNTEER',       1)

    def __init__(self, counsellor_type, num_processes):
        self.counsellor_type = counsellor_type
        self.num_processes = num_processes

#-------------------------------------------------------------------------------

class Priority(enum.Enum):
    '''
        Priority Counselling
    '''

    HIGH =      'High'
    REGULAR =   'Regular'

################################################################################
# Classes
################################################################################

class Counsellor:
    '''
        Class to create counsellor instances

        each counsellor is assigned a role, an id, a shift, 
        and an adhoc duty shift (if available)
    '''

    lunch_break = 60 # 60 minute lunch break

    def __init__(self, env, counsellor_id, shift):
        '''
            param:

            env - simpy environment instance
            counsellor_id - an assigned counsellor id (INTEGER)
            shift - counsellor shift (one of Shifts enum)
        '''

        self.env = env
        self.counsellor_id = f'{counsellor_id}'
        self.lunched = False # whether worker had lunch

        self.adhoc_completed = False # whether worker had completed adhoc duty time slice
        self.adhoc_duty = None # to be set later
        
        self.shift = shift
        self.shift_remaining = shift.duration
        self.role = None # to be set later
        self.priority = None # to be set later

#     #---------------------------------------------------------------------------
#     # Interrupts and Interrupt Service Routines (ISR)
#     #---------------------------------------------------------------------------

# #     def schedule(self):
# #         '''
# #             counsellor schedule at a case-by-case basis
# #         '''
# #         schedule_adhoc_duty(self.adhoc_duty.start) # edge case
# #         schedule_lunch(self.shift.lunch_start % MINUTES_PER_DAY) # edge case


# #         while True:
# #             while self.shift_remaining > 0:
# #                 try:
# #                     # in idle state
# #                     start = self.env.now
# #                     yield self.env.timeout(self.shift_remaining)
# #                     self.shift_remaining = 0

                
# #                     cause = interrupt.cause

# #                     if cause is JobStates.SIGNOUT:
# #                         with state.request(priority=cause.priority) as state:
# #                             yield state & self.env.timeout(self.shift.duration)

# #                         print(f'{self.counsellor_id} shift starts at {self.env.now}')


# #                     elif cause is JobStates.AD_HOC:
# #                         self.adhoc_completed = True

# #                         with state.request(priority=cause.priority) as state:
# #                             yield state & self.env.timeout(self.adhoc_duty.duration)
# # self.env.process(handle_adhoc_jobs(MINUTES_PER_DAY) )


# # self.process.interrupt(JobStates.AD_HOC)

# #                     elif cause is JobStates.LUNCH:
# #                         # give lunch break
# #                         print(f'{self.counsellor_id} Requesting a lunch break at '
# #                             f'{self.env.now}')

# #                         self.lunched = True

# #                         with self.store_counsellors_active.get(lambda x: x==self.user) as request:

# #                         yield request & self.env.timeout(self.lunch_break)
# # self.env.process(handle_lunch_break(MINUTES_PER_DAY) )


# # self.process.interrupt(JobStates.LUNCH) 

# #                     elif cause is JobStates.COUNSELLING:


# #                     # update shift_remaining
# #                     self.shift_remaining -= self.env.now - start

#     #---------------------------------------------------------------------------

#     def handle_adhoc_jobs(self, delay):
#         '''
#             Handle to schedule Ad Hoc Jobs after a certain delay

#             param: delay - delay (Integer or float)
#         '''
#         if not self.adhoc_completed and self.shift in (Shifts.AM, Shifts.PM):
#             try:
#                 yield self.env.timeout(delay)
#                 self.adhoc_completed = True

#             # adhoc duty is interrupted
#             except simpy.Interrupt as interrupt:
#                 print(f'Ad Hoc Duty is interrupted at {self.env.now} '
#                     f'due to {interrupt.cause}')            

#     #---------------------------------------------------------------------------

#     def handle_lunch_break(self, delay):
#         '''
#             handle to give counsellors a lunch break after a certain delay

#             param: delay - delay (Integer or float)
#         '''
#         if not self.lunched and self.shift_remaining < 240:
#             try:
#                 yield self.env.timeout(delay)
#                 self.lunched = True
#             except simpy.Interrupt as interrupt:
#                 print(f'Lunch Break is interrupted at {self.env.now} '
#                     f'due to {interrupt.cause}')    

#--------------------------------------------------------end of Counsellor class

class ServiceOperation:
    '''
        Class to emulate OpenUp Service Operation with a limited number of 
        counsellors to handle helpseeker chat requests during different shifts

        Helpseekers have to request a counsellor to begin the counselling
        process
    '''

    total_recruits = 0 # total number counsellors recruited
    for shift in list(Shifts):
        total_recruits += shift.capacity


    # mean times for random draws (type FLOATS or LIST of FLOATs)
    mean_interarrival_time = [
        7.42, 9.11, 16.39, 22.21, 
        31.19, 44.78, 77.83, 43.73,
        28.71, 26.65, 29.02, 22.29,
        15.89, 16.02, 12.50, 13.47,
        14.58, 12.92, 9.97, 8.95,
        7.09, 9.00, 7.04, 7.05] # mean time vector, size = 24
                                # each entry is associated with helpseekers 
                                # interarrivals at different hours in a day
    
    mean_renege_time = 7.0  # mean patience before reneging
    mean_chat_duration = 60.0 # average chat no longer than 60 minutes
    
    #---------------------------------------------------------------------------

    def __init__(self, *, env):
        '''
            init function

            param: env - simpy environment
        '''
        self.env = env

        # counters
        self.helpseeker_id = 0 # to be changed in create_helpseekers()
        self.reneged = 0
        self.served = 0
        self.reneged_g_repeated = 0
        self.reneged_g_regular = 0
        self.served_g_repeated = 0
        self.served_g_regular = 0
 

        # service operation is given an infinite counsellor intake capacity
        # to accomodate four counsellor shifts (see enum Shifts for details)
        self.store_counsellors_active = simpy.FilterStore(env)

        # create counsellors
        self.counsellors = {}
        for s in Shifts:
            self.counsellors[s] = []
            self.create_counsellors(s)

        self.counsellor_procs = [self.env.process(
            self.set_counsellors_shift(s) ) for s in Shifts]
        # print(self.counsellor_procs)

        # generate helpseekers
        # this process will not be disrupted even when counsellors sign out
        self.helpseeker_procs = self.env.process(self.create_helpseekers() )
        print(self.helpseeker_procs)

    ############################################################################
    # counsellor related functions
    ############################################################################

    def create_counsellors(self, shift):
        '''
            subroutine to create counsellors during a shift

            param:
            shift - one of Shifts enum
        '''

        # signing in involves creating multiple counsellor processes
        for id_ in range(1, shift.capacity+1):
            for subprocess_num in range(1, MAX_NUM_SIMULTANEOUS_CHATS+1):
                counsellor_id = f'{shift.shift_name}_{id_}_{subprocess_num}'
                self.counsellors[shift].append(
                    Counsellor(self.env, counsellor_id, shift)
                )


        print(f'create_counsellors shift:{shift.shift_name}\n{self.counsellors[shift]}\n\n')

    #---------------------------------------------------------------------------

    def set_counsellors_shift(self, shift):
        '''
            routine to sign in and sign out counsellors from a shift

            param:
            shift - one of Shifts enum
        '''
                
        yield self.env.timeout(shift.start) # delay for shift.start minutes
        while True:
            # sign_in
            for counsellor in self.counsellors[shift]:
                print(f'\n{Colors.GREEN}+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}')
                print(f'{Colors.GREEN}Counsellor {counsellor.counsellor_id} signed in at t = {self.env.now}{Colors.WHITE}')
                print(f'{Colors.GREEN}+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}\n')

                yield self.store_counsellors_active.put(counsellor)

            print(f'Signing in shift:{shift.shift_name}  at {self.env.now}.  Active SO counsellors:')
            pprint(self.store_counsellors_active.items)
            print()


            # delay for shift.duration minutes
            yield self.env.timeout(shift.duration) 


            # sign_out
            for _ in self.counsellors[shift]:
                counsellor = yield self.store_counsellors_active.get()
                    # lambda x: counsellor.shift == shift)
                print(f'\n{Colors.RED}-----------------------------------------------------------{Colors.WHITE}')
                # print(f'{Colors.RED}Counsellor {counsellor.counsellor_id} signed out at t = {self.env.now}{Colors.WHITE}')
                print(f'{Colors.RED}Counsellor {counsellor} signed out at t = {self.env.now}{Colors.WHITE}')
                print(f'{Colors.RED}-----------------------------------------------------------{Colors.WHITE}\n')
            print(f'Signing out shift:{shift.shift_name} at {self.env.now}.  Active SO counsellors:\n')
            pprint(self.store_counsellors_active.items)
            print()

            # repeat every 24 hours
            yield self.env.timeout(shift.offset)

    ############################################################################
    # helpseeker related functions
    ############################################################################

    def create_helpseekers(self):
        '''
            function to generate helpseekers in the background
            by interarrival_time to mimic helpseeker interarrivals
        '''
        self.helpseeker_id = 1
        while True:
            renege_time = self.assign_renege_time()
            chat_duration = self.assign_chat_duration()
            risklevel = self.assign_risklevel()
            user_status = self.assign_user_status()

            helpseeker_process = self.handle_helpseeker(
                self.helpseeker_id, 
                renege_time, 
                chat_duration,
                risklevel,
                user_status
            )
            self.env.process(helpseeker_process)
            print(f'{Colors.HGREEN}Helpseeker {self.helpseeker_id}-{risklevel}-{user_status} just entered chatroom at {self.env.now}{Colors.HEND}\n')

            
            interarrival_time = self.assign_interarrival_time()
            yield self.env.timeout(interarrival_time)

            self.helpseeker_id += 1

    #-------------------------------------------------------------------------------

    def handle_helpseeker(self, 
        helpseeker_id, 
        renege_time, 
        chat_duration,
        risklevel,
        helpseeker_status):

        '''
            helpseeker process handler

            param:
                helpseeker_id - helpseeker id
                renege_time - renege time
                chat_duration - chat duration
                risklevel - risk level (one of enum Risklevel)
                helpseeker_status - helpseeker status (one of enum Users)
        '''
        print(f'Helpseeker {helpseeker_id} has accepted TOS.  '
            f'Chat session created at t = {self.env.now}.')


        # wait for a counsellor or renege
        # with self.store_counsellors_active.get(
        #     lambda x: x.priority==counsellor_role) as counsellor:
        print(f'Number of Active SO counsellors: {len(self.store_counsellors_active.items )}')
        print('\n\n\n')
        with self.store_counsellors_active.get() as counsellor:
            print(counsellor)
            # counsellor = self.store_counsellors_active.get()
            results = yield counsellor | self.env.timeout(renege_time)
            # print(f'Results: {results}')
            # print(f'counsellor: {counsellor.resource}')

            if counsellor not in results: # if helpseeker reneged
                print(f'{Colors.HRED}Helpseeker {helpseeker_id} reneged after '
                    f'spending t = {renege_time} minutes in the queue.{Colors.HEND}')
                self.reneged += 1 # update counter
                if helpseeker_status is Users.REPEATED:
                    self.reneged_g_repeated += 1
                else:
                    self.reneged_g_regular += 1
                # context manager will automatically cancel counsellor request

            else: # if counsellor takes in a helpseeker   
                print(f'Helpseeker {helpseeker_id} is assigned to '
                    f'{counsellor} at {self.env.now}')

                yield self.env.timeout(chat_duration)

                # put the counsellor back into the store, so it will be available
                # to the next helpseeker
                print('\n*****************************')
                print('Releasing Counsellor Resource')
                print('*****************************\n')

                yield self.store_counsellors_active.put(counsellor)
                print(f'{Colors.HBLUE}Helpseeker {helpseeker_id}\'s counselling session lasted t = '
                    f'{chat_duration} minutes.\nCounsellor {counsellor} is now available.{Colors.HEND}')

                self.served += 1 # update counter
                if helpseeker_status is Users.REPEATED:
                    self.served_g_repeated += 1
                else:
                    self.served_g_regular += 1

    ############################################################################
    # Predefined Distribution Getters
    ############################################################################

    def assign_interarrival_time(self):
        '''
            Getter to assign interarrival time by the current time interval
            interarrival time follows an exponential distribution
        '''
        
        # cast this as integer to get a rough estimate
        # calculate the nearest hour as an integer
        # use it to access the mean interarrival time, from which the lambda
        # can be calculated
        current_day_minutes = int(self.env.now) % MINUTES_PER_DAY
        nearest_hour = int(current_day_minutes / MINUTES_PER_DAY)
        lambda_interarrival = 1.0 / self.mean_interarrival_time[nearest_hour]
        return random.expovariate(lambda_interarrival)

    #---------------------------------------------------------------------------

    def assign_renege_time(self):
        '''
            Getter to assign patience to helpseeker
            helpseeker patience follows an exponential distribution
        '''
        lambda_renege = 1.0 / self.mean_renege_time
        return random.expovariate(lambda_renege)

    #---------------------------------------------------------------------------

    def assign_chat_duration(self):
        '''
            Getter to assign chat duration
            chat duration follows an exponential distribution
        '''
        lambda_chat_duration = 1.0 / self.mean_chat_duration
        return random.expovariate(lambda_chat_duration)

    #---------------------------------------------------------------------------

    def assign_risklevel(self):
        '''
            Getter to assign risklevels
        '''
        options = list(Risklevels)
        probability = [x.value[1] for x in options]

        return random.choices(options, probability)[0]

    #---------------------------------------------------------------------------

    def assign_user_status(self):
        '''
            Getter to assign user status
        '''
        options = list(Users)
        probability = [x.value[1] for x in options]

        return random.choices(options, probability)[0]

#--------------------------------------------------end of ServiceOperation class

################################################################################
# Main Function
################################################################################

def main():
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    print('Initializing OpenUp Queue Simulation')
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')

    # random.seed(SEED) # comment out line if not reproducing results

    # create environment
    env = simpy.Environment() 

    # set up service operation and run simulation until  
    S = ServiceOperation(env=env)
    env.run(until=SIMULATION_DURATION)
    # print(S.assign_risklevel() )

    print('\n\n\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    print('Final Results')
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    print(f'Total number of Helpseekers visited OpenUp: {S.helpseeker_id}\n')

    print(f'Total number of Helpseekers served: {S.served}')
    print(f'Total number of Helpseekers served given repeated user: {S.served_g_repeated}')
    print(f'Total number of Helpseekers served given regular user: {S.served_g_regular}\n')

    print(f'Total number of Helpseekers reneged: {S.reneged}')
    print(f'Total number of Helpseekers reneged given repeated user: {S.reneged_g_repeated}')
    print(f'Total number of Helpseekers reneged given regular user: {S.reneged_g_regular}')


if __name__ == '__main__':
    main()