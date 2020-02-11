'''
    This program uses Simpy to simulate the OpenUp Counselling Service 
    platform and helpseeker arrivals.  

    Helpseekers will renege when they loose patience waiting in the queue

    For more details about the Simpy syntax, please visit
    https://simpy.readthedocs.io/en/latest/contents.html

    Discrete Event Simulation Primer:
    https://www.academia.edu/35846791/Discrete_Event_Simulation._It_s_Easy_with_SimPy_
'''

import simpy, random, enum, itertools, os
from simpy.util import start_delayed
from pprint import pprint
# from scipy.stats import poisson



INTERARRIVALS_FILE = os.path.expanduser(
    '~/csrp/openup-analysis/interarrivals_day_of_week_hour.csv')

# Globals
QUEUE_THRESHOLD = 5                         # memoize data if queue is >= threshold 
DAYS_IN_WEEK = 7                            # 7 days in a week
MINUTES_PER_HOUR = 60                       # 60 minutes in an hour
MAX_NUM_SIMULTANEOUS_CHATS = 4              # maximum number of simultaneous chats allowed
SEED = 728                                  # for seeding the sudo-random generator
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR     # 1440 minutes per day
SIMULATION_DURATION = MINUTES_PER_DAY * 30  # currently given as num minutes 
                                            #     per day * num days in month

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

    GRAVEYARD = ('GRAVEYARD',   True, 1290, 1890, 840, 3)#2) # from 9:30pm to 7:30am
    AM =        ('AM',          False, 435, 915, 960, 2) #2)   # from 7:15am to 3:15 pm
    PM =        ('PM',          False, 840, 1320, 960, 3)#2)  # from 2pm to 10pm
    SPECIAL =   ('SPECIAL',     True, 1020, 1500, 960, 2)#1) # from 5pm to 1 am

    def __init__(self, shift_name, is_edge_case, start, end, offset, capacity):
        self.shift_name = shift_name
        self.is_edge_case = is_edge_case
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
            counsellor_id - an assigned counsellor id (STRING)
            shift - counsellor shift (one of Shifts enum)
        '''

        self.env = env
        self.counsellor_id = counsellor_id
        self.lunched = False # whether worker had lunch

        self.adhoc_completed = False # whether worker had completed adhoc duty time slice
        self.adhoc_duty = None # to be set later
        
        self.shift = shift
        self.shift_remaining = shift.duration
        self.role = None # to be set later
        self.priority = None # to be set later

    #---------------------------------------------------------------------------
    # Interrupts and Interrupt Service Routines (ISR)
    #---------------------------------------------------------------------------

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
    # __mean_interarrival_time = [6.159314, 7.835689, 10.193088, 14.868265, 
    #     17.56785, 25.164313, 28.825652, 23.254639, 24.467604, 21.350428, 
    #     15.200464, 15.548507, 11.757778, 12.807021, 11.236349, 10.274989,
    #     9.907474, 8.657091, 6.997997, 7.13911, 7.565797, 7.145651, 6.074971, 
    #     5.925648] # mean time vector, size = 24
                  # each entry is associated with helpseeker 
                  # interarrivals at different hours in a day

        # [7.42, 9.11, 16.39, 22.21, 
        # 31.19, 44.78, 77.83, 43.73,
        # 28.71, 26.65, 29.02, 22.29,
        # 15.89, 16.02, 12.50, 13.47,
        # 14.58, 12.92, 9.97, 8.95,
        # 7.09, 9.00, 7.04, 7.05] 
    
    __mean_renege_time = 7.0  # mean patience before reneging
    __mean_chat_duration = 60.0 # average chat no longer than 60 minutes
    __counsellor_postchat_survey = 20 # fixed time to fill out counsellor postchat survey
    
    #---------------------------------------------------------------------------

    def __init__(self, *, env):
        '''
            init function

            param: env - simpy environment
        '''
        self.env = env

        # set interarrivals (a circular array of interarrival times)
        self.__mean_interarrival_time = self.read_interarrivals_csv()
        # print(self.__mean_interarrival_time)

        # counters and flags (also see properties section)
        self.num_helpseekers = 0 # to be changed in create_helpseekers()
        self.reneged = 0
        self.served = 0
        self.reneged_g_repeated = 0
        self.reneged_g_regular = 0
        self.served_g_repeated = 0
        self.served_g_regular = 0
 
        self.helpseeker_in_system = []
        self.helpseeker_queue = []
        self.times_queue_exceeded_five_helpseekers = []

        self.num_available_counsellor_processes = []

        self.helpseeker_queue_max_length = 0


        # service operation is given an infinite counsellor intake capacity
        # to accomodate four counsellor shifts (see enum Shifts for details)
        self.store_counsellors_active = simpy.FilterStore(env)

        # create counsellors
        self.counsellors = {}
        for s in Shifts:
            self.counsellors[s] = []
            self.create_counsellors(s)

        self.counsellor_procs_signin = [self.env.process(
            self.counsellors_signin(s) ) for s in Shifts]

        self.counsellor_procs_signout = [self.env.process(
            self.counsellors_signout(s) ) for s in Shifts]

        # print(self.counsellor_procs)

        # generate helpseekers
        # this process will not be disrupted even when counsellors sign out
        self.helpseeker_procs = self.env.process(self.create_helpseekers() )
        # print(self.helpseeker_procs)


    ############################################################################
    # Properties (for encapsulation)
    ############################################################################

    @property
    def helpseeker_queue_max_length(self):
        return self.__helpseeker_queue_max_length

    @property
    def num_helpseekers(self):
        return self.__num_helpseekers

    @property
    def reneged(self):
        return self.__reneged

    @property
    def reneged_g_repeated(self):
        return self.__reneged_g_repeated

    @property
    def reneged_g_regular(self):
        return self.__reneged_g_regular

    @property
    def served(self):
        return self.__served

    @property
    def served_g_repeated(self):
        return self.__served_g_repeated

    @property
    def served_g_regular(self):
        return self.__served_g_regular

    @helpseeker_queue_max_length.setter
    def helpseeker_queue_max_length(self, value):
        self.__helpseeker_queue_max_length = value

    @num_helpseekers.setter
    def num_helpseekers(self, value):
        self.__num_helpseekers = value

    @reneged.setter
    def reneged(self, value):
        self.__reneged = value

    @reneged_g_repeated.setter
    def reneged_g_repeated(self, value):
        self.__reneged_g_repeated = value

    @reneged_g_regular.setter
    def reneged_g_regular(self, value):
        self.__reneged_g_regular = value

    @served.setter
    def served(self, value):
        self.__served = value

    @served_g_repeated.setter
    def served_g_repeated(self, value):
        self.__served_g_repeated = value

    @served_g_regular.setter
    def served_g_regular(self, value):
        self.__served_g_regular = value

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


        # print(f'create_counsellors shift:{shift.shift_name}\n{self.counsellors[shift]}\n\n')

    #---------------------------------------------------------------------------

    def counsellors_signin(self, shift):
        '''
            routine to sign in counsellors during a shift

            param:
            shift - one of Shifts enum
        '''

        counsellor_init = True

        if not shift.is_edge_case:
            yield self.env.timeout(shift.start) # delay for shift.start minutes

        while True:
            for counsellor in self.counsellors[shift]:
                # print(f'\n{Colors.GREEN}+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}')
                # print(f'{Colors.GREEN}Counsellor {counsellor} signed in at t = {self.env.now}{Colors.WHITE}')
                # print(f'{Colors.GREEN}+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}\n')

                yield self.store_counsellors_active.put(counsellor)

            # print(f'Signing in shift:{shift.shift_name}  at {self.env.now}.  Active SO counsellors:')
            # pprint(self.store_counsellors_active.items)
            # print()

            if counsellor_init and shift.is_edge_case:
                yield self.env.timeout(shift.start)
                counsellor_init = False

            else:
                # repeat every 24 hours
                yield self.env.timeout(MINUTES_PER_DAY)

    #---------------------------------------------------------------------------

    def counsellors_signout(self, shift):
        '''
            routine to sign out counsellors during a shift

            param:
            shift - one of Shifts enum
        '''

        # delay for shift.end minutes
        # taking the mod to deal with first initialized graveyard or special shifts (edge cases) 
        yield self.env.timeout(shift.end % MINUTES_PER_DAY)
        while True:
            for _ in range(shift.capacity * MAX_NUM_SIMULTANEOUS_CHATS):
                counsellor = yield self.store_counsellors_active.get()
                    # lambda x: x.shift is shift)
            #     print(f'\n{Colors.RED}-----------------------------------------------------------{Colors.WHITE}')
            #     print(f'{Colors.RED}Counsellor {counsellor} signed out at t = {self.env.now}{Colors.WHITE}')
            #     # print(f'{Colors.RED}Counsellor {counsellor} signed out at t = {self.env.now}{Colors.WHITE}')
            #     print(f'{Colors.RED}-----------------------------------------------------------{Colors.WHITE}\n')
            # print(f'Signing out shift:{shift.shift_name} at {self.env.now}.  Active SO counsellors:\n')
            # pprint(self.store_counsellors_active.items)
            # print()

            # repeat every 24 hours
            yield self.env.timeout(MINUTES_PER_DAY)

    ############################################################################
    # helpseeker related functions
    ############################################################################

    def create_helpseekers(self):
        '''
            function to generate helpseekers in the background SOURCE
            by interarrival_time to mimic helpseeker interarrivals
        '''

        for i in itertools.count(1): # use this instead of while loop 
                                     # for efficient looping

            # space out incoming helpseekers
            interarrival_time = self.assign_interarrival_time()
            yield self.env.timeout(interarrival_time)

            self.env.process(self.handle_helpseeker(i) )
            self.num_helpseekers += 1 # increment counter

    #---------------------------------------------------------------------------

    def handle_helpseeker(self, helpseeker_id):

        '''
            helpseeker process handler CUSTOMER

            param:
                helpseeker_id - helpseeker id
        '''

        renege_time = self.assign_renege_time()
        chat_duration = self.assign_chat_duration()
        risklevel = self.assign_risklevel()
        helpseeker_status = self.assign_user_status()

        # print(f'{Colors.HGREEN}Helpseeker '
        #         f'{helpseeker_id}-{risklevel}-{helpseeker_status} '
        #         f'has just accepted TOS.  Chat session created at '
        #         f'{self.env.now}{Colors.HEND}\n')

        self.helpseeker_in_system.append(helpseeker_id)
        self.helpseeker_queue.append(helpseeker_id)

        current_helpseeker_queue_length = len(self.helpseeker_queue)
        if current_helpseeker_queue_length > self.helpseeker_queue_max_length:
            self.helpseeker_queue_max_length = current_helpseeker_queue_length
            if current_helpseeker_queue_length >= QUEUE_THRESHOLD:
                # print('here')
                current_time = self.env.now
                current_day_minutes = int(current_time) % MINUTES_PER_DAY
                # print(f'weekday: {int(current_time / MINUTES_PER_DAY)} - hour: {int(current_day_minutes / 60)}')

                self.times_queue_exceeded_five_helpseekers.append(
                    (f'weekday:{int(current_time / MINUTES_PER_DAY) % DAYS_IN_WEEK}',
                    f'hour:{int(current_day_minutes / MINUTES_PER_HOUR)}')
                )

        # print(f'Updated max queue length to {self.helpseeker_queue_max_length}.\n'
        #     f'Helpseeker Queue: {self.helpseeker_queue}\n\n\n')
        
        # wait for a counsellor or renege
        # with self.store_counsellors_active.get(
        #     lambda x: x.priority==counsellor_role) as counsellor:
        #     print(f'Number of Active SO counsellors: {len(self.store_counsellors_active.items )}')
        # print('\n\n\n')
        
        with self.store_counsellors_active.get() as counsellor:
            # print(counsellor)
            # counsellor = self.store_counsellors_active.get()
            results = yield counsellor | self.env.timeout(renege_time)
            # print(f'Results: {results}')
            # print(f'counsellor: {counsellor.resource}')

            # dequeue helpseeker in the waiting queue
            self.helpseeker_queue.remove(helpseeker_id)
            # print(f'Helpseeker Queue: {self.helpseeker_queue}')

            # store number of available counsellor processes at time
            self.num_available_counsellor_processes.append(
                (self.env.now, len(self.store_counsellors_active.items) )
            )
            
            if counsellor not in results: # if helpseeker reneged
                # remove helpseeker from system record
                self.helpseeker_in_system.remove(helpseeker_id)
                # print(f'Helpseeker in system: {self.helpseeker_in_system}')

                # print(f'{Colors.HRED}Helpseeker {helpseeker_id} reneged after '
                #     f'spending t = {renege_time} minutes in the queue.{Colors.HEND}')
                self.reneged += 1 # update counter
                if helpseeker_status is Users.REPEATED:
                    self.reneged_g_repeated += 1
                else:
                    self.reneged_g_regular += 1
                # context manager will automatically cancel counsellor request



            else: # if counsellor takes in a helpseeker
                # print(f'Helpseeker {helpseeker_id} is assigned to '
                #     f'{counsellor} at {self.env.now}')

                yield self.env.timeout(chat_duration)

                # put the counsellor back into the store, so it will be available
                # to the next helpseeker
                # print('\n*****************************')
                # print('Releasing Counsellor Resource')
                # print('*****************************\n')

                # counsellor_id = counsellor.counsellor_id
                # shift = counsellor.shift
                # print(counsellor.__enter__)
                # print(f'{Colors.HBLUE}Helpseeker {helpseeker_id}\'s counselling session lasted t = '
                #     f'{chat_duration} minutes.\nCounsellor {counsellor} is now available.{Colors.HEND}')

                # remove helpseeker from system record
                self.helpseeker_in_system.remove(helpseeker_id)
                # print(f'Helpseeker in system: {self.helpseeker_in_system}')

                # fill out counsellor postchat survey
                yield self.env.timeout(self.__counsellor_postchat_survey)

                yield self.store_counsellors_active.put(counsellor)

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
            interarrival time follows the exponential distribution
        '''
        
        # cast this as integer to get a rough estimate
        # calculate the nearest hour as an integer
        # use it to access the mean interarrival time, from which the lambda
        # can be calculated

        # print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
        # print('INTERARRIVALS')
        # print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')

        current_time = int(self.env.now)
        # print(f'Current time: {current_time}')

        current_weekday = int(current_time / MINUTES_PER_DAY)
        # print(f'Current weekday: {current_weekday}')


        current_day_minutes = current_time % MINUTES_PER_DAY
        # print(f'Current Minutes day: {current_day_minutes}')
        nearest_hour = int(current_day_minutes / 60)
        # print(f'Nearest hour: {nearest_hour}')
        
        # get the index
        idx = int(24*current_weekday + nearest_hour) % \
            len(self.__mean_interarrival_time)
        # print(f'index: {idx}')

        lambda_interarrival = 1.0 / self.__mean_interarrival_time[idx]
        # return random.gammavariate(50, lambda_interarrival)
        return random.expovariate(lambda_interarrival)

    #---------------------------------------------------------------------------

    def assign_renege_time(self):
        '''
            Getter to assign patience to helpseeker
            helpseeker patience follows an exponential distribution
        '''
        lambda_renege = 1.0 / self.__mean_renege_time
        return random.expovariate(lambda_renege)

    #---------------------------------------------------------------------------

    def assign_chat_duration(self):
        '''
            Getter to assign chat duration
            chat duration follows the gamma distribution (exponential if a=1)
        '''
        lambda_chat_duration = 1.0 / self.__mean_chat_duration
        return random.expovariate(lambda_chat_duration)
        # return random.gammavariate(2, lambda_chat_duration)

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

    #---------------------------------------------------------------------------

    def read_interarrivals_csv(self):
        '''
            file input function to read in interarrivals data
            by the day, starting from Sunday 0h, ending at Saturday 23h      
        '''
        try:
            with open(INTERARRIVALS_FILE, 'r') as f:
                weekday_hours = [float(i.split(',')[-1][:-1])
                    for i in f.readlines()[1:] ]

            return weekday_hours

        except Exception as e:
            print('Unable to read interarrivals file.')

#--------------------------------------------------end of ServiceOperation class

################################################################################
# Main Function
################################################################################

def main():
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    print('Initializing OpenUp Queue Simulation')
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')

    random.seed(SEED) # comment out line if not reproducing results

    # create environment
    env = simpy.Environment() 

    # set up service operation and run simulation until  
    S = ServiceOperation(env=env)
    env.run(until=SIMULATION_DURATION)
    # print(S.assign_risklevel() )

    # print('\n\n\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    # print(f'Iteration #{i} ')
    # print(f'Final Results -- number of simultaneous chats: {MAX_NUM_SIMULTANEOUS_CHATS}')
    # print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    # print(f'Total number of Helpseekers visited OpenUp: {S.num_helpseekers}\n')

    # percent_served = S.served/S.num_helpseekers * 100
    # print(f'Total number of Helpseekers served: {S.served} ({percent_served:.02f}%)')
    # print(f'Total number of Helpseekers served -- repeated user: {S.served_g_repeated}')
    # print(f'Total number of Helpseekers served -- user: {S.served_g_regular}\n')

    # percent_reneged = S.reneged/S.num_helpseekers * 100
    # print(f'Total number of Helpseekers reneged: {S.reneged} ({percent_reneged:.02f}%)')
    # print(f'Total number of Helpseekers reneged -- repeated user: {S.reneged_g_repeated}')
    # print(f'Total number of Helpseekers reneged -- user: {S.reneged_g_regular}\n')

    # print(f'Maximum helpseeker queue length: {S.helpseeker_queue_max_length}')
    # print(f'Number of instances at least five helpseekers are waiting in the queue: {len(S.times_queue_exceeded_five_helpseekers)}')
    # print(f'full details: {S.times_queue_exceeded_five_helpseekers}')

if __name__ == '__main__':
    main()