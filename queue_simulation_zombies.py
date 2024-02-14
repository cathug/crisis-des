'''
    This program uses Simpy to simulate the OpenUp Counselling Service 
    platform and user arrivals.  

    Users will renege when they loose patience waiting in the queue

    Interrupt version with zombies - When counsellors have to sign out, an interrupt
    is thrown to end the existing chat process, and the helpseeker
    will be transferred to the next available counsellor or reneged
    when the user's patience dries up.  Counsellors will not work overtime.
    
    For more details about the Simpy syntax, please visit
    https://simpy.readthedocs.io/en/latest/contents.html

    Discrete Event Simulation Primer:
    https://www.academia.edu/35846791/Discrete_Event_Simulation._It_s_Easy_with_SimPy_

    International Worker Meal and Teabreak Standards:
    https://www.ilo.org/wcmsp5/groups/public/---ed_protect/---protrav/---travail/documents/publication/wcms_491374.pdf
'''

import simpy, random, enum, itertools, os, logging
from simpy.util import start_delayed
from pprint import pprint
from scipy.stats import beta as betavariate
from simpy.events import AllOf
from statsmodels.tsa.statespace.structural import UnobservedComponents
from scipy.stats import boxcox
from scipy.special import inv_boxcox
import pandas as pd
import numpy as np
from dataclasses import dataclass


logging.basicConfig(
    level=logging.ERROR,
    # format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    format='%(message)s',
    filename='debug.log'
)


NOV_INTERARRIVALS = os.path.expanduser(
    '~/csrp/openup-queue-simulation/real_interarrivals_nov.csv')


INTERARRIVALS_FILE = os.path.expanduser(
    '~/csrp/openup-queue-simulation/interarrivals_day_of_week_hour/Oct2020_to_Nov2020/interarrivals_day_of_week_hour.csv')

################################################################################ 
# Globals
################################################################################

QUEUE_THRESHOLD = 0                         # memoize data if queue length is >= threshold 
DAYS_IN_WEEK = 7                            # 7 days in a week
MINUTES_PER_HOUR = 60                       # 60 minutes in an hour

MAX_SIMULTANEOUS_CHATS = {
    'SOCIAL_WORKER': 3,                     # Social Worker can process max 3 chats
    'SOCIAL_WORKER2': 3,                    # Social Worker can process max 3 chats
    'DUTY_OFFICER': 1,                      # Duty Officer can process max 1 chat
    'VOLUNTEER': 2,                         # Volunteer can process max 2 chat
}    

SEED = 728                                  # for seeding the global sudo-random generator
THINNING_SEED = 305                         # for seeding the thing algo sudo-random generator
OFFSET = 744

MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR     # 1440 minutes per day
SIMULATION_DURATION = MINUTES_PER_DAY * 30  # currently given as num minutes 
                                            #     per day * num days in month

POSTCHAT_FILLOUT_TIME_IF_SERVED = 20        # time to fill out counsellor postchat if user has been served
POSTCHAT_FILLOUT_TIME_IF_RENEGED = 5        # time to fill out counsellor postchat if user has reneged

# # counsellor average chat no longer than 60 minutes
# # meaning differences between types of 1/mean_chat_duration will be negligible
# MEAN_CHAT_DURATION_COUNSELLOR = {
#     'SOCIAL_WORKER': 51.4,                  # Social Worker - average 51.4 minutes
#     'SOCIAL_WORKER2': 51.4,                  # Social Worker - average 51.4 minutes
#     'DUTY_OFFICER': 56.9,                   # Duty Officer - average 56.9 minutes
#     'VOLUNTEER': 57.2,                      # Volunteer - average 57.2 minutes
# }


MEAL_BREAK_DURATION = 60                    # 60 minute meal break
LAST_CASE_CUTOFF = 10                       # do not assign any more cases 30 minutes before signoff

LEN_CIRCULAR_ARRAY = 20000                  # length of circular array
MAX_CHAT_DURATION = 60 * 11                 # longest chat duration is 11 hours (from OpenUp 1.0)

VALIDATE_CHAT_THRESHOLD = 7.5               # time elapsed in minutes to have a pingpong>=4 

################################################################################
# Enums, structs and constants
################################################################################

class Colors:
    '''
        Color codes for terminal
    '''
    GREEN =  '\033[32m'
    RED =  '\033[91m'
    WHITE = '\033[0m'
    BLUE = '\033[94m'
    YELLOW = '\033[33m'

    HGREEN = '\x1b[6;37;42m'
    HRED = '\x1b[6;37;41m'
    HWHITE = '\x1b[6;37;47m'
    HBLUE = '\x1b[6;37;44m'
    HEND = '\x1b[0m'

#-------------------------------------------------------------------------------

class Shifts(enum.Enum):
    '''
        Enum Shifts
    '''
    GRAVEYARD   = enum.auto()
    AM          = enum.auto()
    PM          = enum.auto()
    SPECIAL     = enum.auto()

#-------------------------------------------------------------------------------

class Roles(enum.Enum):
    '''
        Counsellor Roles
    '''

    SOCIAL_WORKER   = ('SOCIAL_WORKER',   True,   True, False)
    SOCIAL_WORKER2  = ('SOCIAL_WORKER2',  True,   True, False)
    DUTY_OFFICER    = ('DUTY_OFFICER',    True,   True, False)
    VOLUNTEER       = ('VOLUNTEER',       False,  True, False)
    RELIEF_WORKER   = ('RELIEF_WORKER',   False,  True, False)

    def __init__(self, counsellor_type, meal_break, 
        first_tea_break, last_tea_break):
        self.counsellor_type = counsellor_type
        self.num_processes = MAX_SIMULTANEOUS_CHATS.get(counsellor_type)
        # self.mean_chat_duration = MEAN_CHAT_DURATION_COUNSELLOR.get(
        #     counsellor_type)
        self.meal_break = meal_break
        self.first_tea_break = first_tea_break
        self.last_tea_break = last_tea_break

#-------------------------------------------------------------------------------

@dataclass
class CounsellorShift:
    '''
        CounsellorShift dataclass or struct
        documenting shift, role, start and end time of the shift
    '''
    shift: Shifts       # one of Shifts enum
    role: Roles         # one of Roles enum

    is_edge_case: bool  # if time specified is edge case
    start: int          # start time of shift
    end: int            # end time of shift
    num_workers: int    # number of workers in shift

    
    @property
    def duration(self):
        return int(self.end - self.start)

    @property
    def meal_start(self):
        '''
            define lunch as the midpoint of shift
            which is written to minimize underflow and overflow problems
        '''
        if self.role is Roles.VOLUNTEER or self.role.meal_break is False:
            return None

        if self.shift is Shifts.GRAVEYARD:
            if self.role in [Roles.SOCIAL_WORKER, Roles.DUTY_OFFICER]:
                # this is adjusted with set value of DutyOfficerShift.GRAVEYARD
                return self.start + 240 - 15 # 1:15am
            elif self.role is Roles.SOCIAL_WORKER2:
                # this is adjusted with set value of DutyOfficerShift.GRAVEYARD
                return self.end - 180 - 15 # so wakes up at 7:15am

        # else:
        return int(self.start + (self.end - self.start) / 2) % MINUTES_PER_DAY

#-------------------------------------------------------------------------------

class JobStates(enum.Enum):
    '''
        Counsellor in three states:
        counselling, eating lunch, and signout,
        each of which are given different priorities (must be integers)

        The higher the priority, the lower the value 
        (10 has higher priority than 20)
    '''

    SIGNOUT =       ('SIGN_OUT', 'signed out',              10)
    # CHAT =          ('CHAT',            30)
    MEAL_BREAK =    ('MEAL_BREAK', 'taking a break (AFK)',  20)

    def __init__(self, job_name, status, priority):
        self.job_name = job_name
        self.status = status
        self.priority = priority

#-------------------------------------------------------------------------------

class Risklevels(enum.Enum):
    '''
        Distribution of LOW/MEDIUM/HIGH/CRISIS
    '''
    # nested tuple order - p, alpha, beta, shape
    # risk enum | risklevel | non-repeated data | repeated data
    CRISIS =    ('CRISIS',  ( .0,  3.89, 82.4, 1991.3 ), 
        ( .0,   2.29, 84774.16, 4481110.5 ) )
    HIGH =      ('HIGH',    ( .007, 3.89, 82.4, 1991.3 ),
        ( .006, 2.29, 84774.16, 4481110.5 ) )
    MEDIUM =    ('MEDIUM', ( .102, 2.17, 5.32, 250.6 ),
        ( .097, 2.17, 5.32, 250.6 ) )
    LOW =       ('LOW',     ( .891, 1.67, 4.64, 190.5 ),
        ( .897, 1.67, 4.64, 190.5 ) )

    def __init__(self, risk,
        non_repeated_user_data, repeated_user_data):
        self.risk = risk

        self.p_non_repeated_user = non_repeated_user_data[0]
        self.alpha_non_repeated_user  = non_repeated_user_data[1]
        self.beta_non_repeated_user = non_repeated_user_data[2]
        self.shape_non_repeated_user = non_repeated_user_data[3]

        self.p_repeated_user = repeated_user_data[0]
        self.alpha_repeated_user  = repeated_user_data[1]
        self.beta_repeated_user = repeated_user_data[2]
        self.shape_repeated_user = repeated_user_data[3]
        
#-------------------------------------------------------------------------------

class Users(enum.Enum):
    '''
        Distribution of Repeated Users - 71.2% regular / 28.8% repeated
        among the users accepting TOS
    '''
    # nested tuple order - p, mean, variance
    # user enum | user status | user index | probability, mean patience
    REPEATED =      ('REPEATED_USER',       1, (.288, 5.29) )
    NON_REPEATED =  ('NONREPEATED_USER',    2, (.712, 3.45) )
    
    def __init__(self, user_type, index, user_data):
        self.user_type = user_type
        self.index = index # index to access Risklevel probability
        self.probability = user_data[0]
        self.mean_patience = user_data[1]



# class Users(enum.Enum):
#     '''
#         Distribution of Repeated Users - 71.2% regular / 28.8% repeated
#         among the users accepting TOS
#     '''
#     # nested tuple order - p, mean, variance
#     # user enum | user status | user index | probability, alpha, beta, loc, shape
#     REPEATED =      ('REPEATED_USER',       1,
#         ( .288, .588, 29.1, .174, 351.1 ) )
#     NON_REPEATED =  ('NONREPEATED_USER',    2,
#         ( .712, .739, 3.31, .175, 13.6 ) )
    
#     def __init__(self, user_type, index, user_data):
#         self.user_type = user_type
#         self.index = index # index to access Risklevel probability
#         self.probability = user_data[0]

#         self.alpha_renege_time = user_data[1]
#         self.beta_renege_time = user_data[2]
#         self.loc_renege_time = user_data[3]
#         self.shape_renege_time = user_data[4]

#-------------------------------------------------------------------------------

class TOS(enum.Enum):
    '''
        TOS States - here "TOS REJECTED" includes all "TOS NOT ACCEPTED" cases
    '''

    # TOS enum | TOS status
    TOS_ACCEPTED =  ('TOS_ACCEPTED', .748)
    TOS_REJECTED =  ('TOS_REJECTED', .252)
    
    def __init__(self, status, probability):
        self.status = status
        self.probability = probability

################################################################################
# Classes
################################################################################

class Counsellor:
    '''
        Class to create counsellor instances

        each counsellor is assigned a role, an id, a shift, 
        and an adhoc duty shift (if available)
    '''

    def __init__(self, env, counsellor_id, counsellor_shift):
        '''
            param:

            env - simpy environment instance
            counsellor_id - an assigned counsellor id (STRING)
            counsellor_shift - CounsellorShift DataClass instance
        '''

        self.env = env
        self.counsellor_id = counsellor_id
        self.counsellor_shift = counsellor_shift
        self.client_id = None # stores client id for interrupting chats

    ############################################################################
    # Properties (for encapsulation)
    ############################################################################

    @property
    def client_id(self):
        return self.__client_id

    @client_id.setter
    def client_id(self, value):
        if isinstance(value, (int, type(None) ) ):
            self.__client_id = value

    def reset(self):
        self.client_id = None

#--------------------------------------------------------end of Counsellor class

class ServiceOperation:
    '''
        Class to emulate OpenUp Service Operation with a limited number of 
        counsellors to handle user chat requests during different shifts

        Users have to request a counsellor to begin the counselling
        process
    '''

    def __init__(self, *, env, 
        volunteer_shifts,
        duty_officer_shifts,
        social_worker_shifts,
        ts, ts_period, thinning_random,
        boxcox_lambda=None, 
        postchat_fillout_time_if_served=POSTCHAT_FILLOUT_TIME_IF_SERVED,
        postchat_fillout_time_if_reneged=POSTCHAT_FILLOUT_TIME_IF_RENEGED,
        meal_break_duration=MEAL_BREAK_DURATION,
        valid_chat_threshold=VALIDATE_CHAT_THRESHOLD,
        use_actual_interarrivals=False):

        '''
            init function

            param:
                env - simpy environment

                volunteer_shifts - list of CounsellorShift instances

                duty_officer_shifts - list of CounsellorShift instances

                social_worker_shifts - list of CounsellorShift instances

                ts - fitted time series model (a statsmodel object)

                ts_period - period in the specified time series (an integer)

                boxcox_lambda - the fitted lambda variable, if available
                    default is set to None

                postchat_fillout_time_if_served - Time alloted to complete the counsellor
                    postchat.  If not specified, defaults to POSTCHAT_FILLOUT_TIME_IF_SERVED

                meal_break_duration - Meal break duration in minutes.
                    If not specified, defaults to MEAL_BREAK_DURATION

                valid_chat_threshold - how much time elapsed before case is counted as valid chat
        '''

        if use_actual_interarrivals:
            self.interarrivals = self.read_interarrival_time()

        self.time_series = ts
        self.time_series_period = ts_period
        self.boxcox_lambda = boxcox_lambda
        self.thinning_random = thinning_random

        self.valid_chat_threshold = valid_chat_threshold

        self.env = env

        self.__counsellor_postchat_survey_time_if_served = postchat_fillout_time_if_served
        self.__counsellor_postchat_survey_time_if_reneged = postchat_fillout_time_if_reneged
        self.__meal_break = meal_break_duration


        # counters and flags (also see properties section)
        self.num_users = 0 # to be changed in create_users()
        self.num_users_TOS_accepted = 0
        self.num_users_TOS_rejected = 0


        self.reneged = 0
        self.reneged_during_transfer = 0
        self.served = 0
        self.served_g_repeated = 0
        self.served_g_regular = 0
        self.served_g_valid = 0


        self.users_in_system = []
        self.user_queue = []
        self.queue_status = []
        self.queue_time_stats = []
        self.renege_time_stats = []

        self.queue_time_stats_transfer = []  # for users sent back to queue
        self.renege_time_stats_transfer = [] # for users sent back to queue

        self.case_chat_time = []

        self.num_available_counsellor_processes = []

        self.user_queue_max_length = 0


        self.current_shift_start = {
            Shifts.GRAVEYARD: None,
            Shifts.AM: None,
            Shifts.PM: None,
            Shifts.SPECIAL: None
        }

        self.current_shift_end = {
            Shifts.GRAVEYARD: None,
            Shifts.AM: None,
            Shifts.PM: None,
            Shifts.SPECIAL: None
        }


        # self.processes = {} # the main idle process
        self.counsellor_procs_signin = {}
        self.counsellor_procs_signout = {}


        # # other processes to interrupt the main process
        self.meal_break_processes = {}
        

        # service operation is given an infinite counsellor intake capacity
        # to accomodate four counsellor shifts (see enum Shifts for details)
        self.store_counsellors_active = simpy.FilterStore(env)
        self.counsellor_user_mapping = {}


        # self.signout_ready_flag = {}
        # for s in DutyOfficerShifts:
        #     self.signout_ready_flag[s] = None


        # create list of counsellors at different shifts
        

        
        self.counsellors = {}
        for r in Roles:
            self.counsellors[r] = {}

        for s in volunteer_shifts:
            self.counsellors[s.role][s.shift] = []
            self.list_counsellers(s)

        for s in duty_officer_shifts:
            self.counsellors[s.role][s.shift] = []
            self.list_counsellers(s)

        for s in social_worker_shifts:
            self.counsellors[s.role][s.shift] = []
            self.list_counsellers(s)


        # logging.debug(f'Counsellors Arranged:\n{self.counsellors}')

        # set up idle processes
        self.counsellor_procs_signin[Roles.DUTY_OFFICER] = [self.env.process(
            self.counsellors_signin(s) ) for s in duty_officer_shifts]

        self.counsellor_procs_signout[Roles.DUTY_OFFICER] = [self.env.process(
            self.counsellors_signout(s) ) for s in duty_officer_shifts]



        self.counsellor_procs_signin[Roles.SOCIAL_WORKER] = [self.env.process(
            self.counsellors_signin(s) ) for s in social_worker_shifts]

        self.counsellor_procs_signout[Roles.SOCIAL_WORKER] = [self.env.process(
            self.counsellors_signout(s) ) for s in social_worker_shifts]



        self.counsellor_procs_signin[Roles.VOLUNTEER] = [self.env.process(
            self.counsellors_signin(s) ) for s in volunteer_shifts]

        self.counsellor_procs_signout[Roles.VOLUNTEER] = [self.env.process(
            self.counsellors_signout(s) ) for s in volunteer_shifts]


        # set up meal breaks
        self.meal_break_processes[Roles.SOCIAL_WORKER] = [self.env.process(
            self.counsellors_break_start(s) ) for s in social_worker_shifts]

        self.meal_break_processes[Roles.SOCIAL_WORKER] = [self.env.process(
            self.counsellors_break_end(s) ) for s in social_worker_shifts]


        self.meal_break_processes[Roles.DUTY_OFFICER] = [self.env.process(
            self.counsellors_break_start(s) ) for s in duty_officer_shifts]

        self.meal_break_processes[Roles.DUTY_OFFICER] = [self.env.process(
            self.counsellors_break_end(s) ) for s in duty_officer_shifts]


        # generate users
        # this process will not be disrupted even when counsellors sign out
        self.user_handler = [None] * LEN_CIRCULAR_ARRAY
        self.user_procs = self.env.process(self.create_users() )
        # logging.debug(self.user_procs)


    ############################################################################
    # Properties (for encapsulation)
    ############################################################################

    @property
    def user_queue_max_length(self):
        return self.__user_queue_max_length

    @property
    def num_users(self):
        return self.__num_users

    @property
    def reneged(self):
        return self.__reneged

    @property
    def reneged_during_transfer(self):
        return self.__reneged_during_transfer

    @property
    def served(self):
        return self.__served

    @property
    def served_g_repeated(self):
        return self.__served_g_repeated

    @property
    def served_g_regular(self):
        return self.__served_g_regular

    @property
    def served_g_valid(self):
        return self.__served_g_valid
    
    @property
    def case_chat_time(self):
        return self.__case_chat_time
    

    @user_queue_max_length.setter
    def user_queue_max_length(self, value):
        self.__user_queue_max_length = value

    @num_users.setter
    def num_users(self, value):
        self.__num_users = value

    @reneged.setter
    def reneged(self, value):
        self.__reneged = value

    @reneged_during_transfer.setter
    def reneged_during_transfer(self, value):
        self.__reneged_during_transfer = value

    @served.setter
    def served(self, value):
        self.__served = value

    @served_g_repeated.setter
    def served_g_repeated(self, value):
        self.__served_g_repeated = value

    @served_g_regular.setter
    def served_g_regular(self, value):
        self.__served_g_regular = value

    @served_g_valid.setter
    def served_g_valid(self, value):
        self.__served_g_valid = value   

    @case_chat_time.setter
    def case_chat_time(self, value):
        self.__case_chat_time = value

    ############################################################################
    # counsellor related functions
    ############################################################################

    def list_counsellers(self, counsellor_shift):
        '''
            subroutine to create list of counsellors with a certain counsellor shift

            param:
            counsellor_shift - CounsellorShift DataClass
        '''            

        # signing in involves creating multiple counsellor processes
        for id_ in range(1, counsellor_shift.num_workers+1):
            for subprocess_num in range(1, counsellor_shift.role.num_processes+1):
                counsellor_id = f'{counsellor_shift.shift.name}_{counsellor_shift.role.counsellor_type}_{id_}_process_{subprocess_num}'
                self.counsellors[counsellor_shift.role][counsellor_shift.shift].append(
                    Counsellor(self.env, counsellor_id, counsellor_shift)
                )
                
                # logging.debug(f'list_counsellers shift:{counsellor_shift.shift}\n{self.counsellors[counsellor_shift.role][counsellor_shift.shift]}\n\n')

    #---------------------------------------------------------------------------

    def counsellors_signin(self, counsellor_shift):
        '''
            routine to sign in counsellors during a shift

            param:
            counsellor_shift - CounsellorShift DataClass instance
        '''
        counsellor_init = True
        shift_remaining = None


        # start shift immediately if graveyard or special shift to account for edge case
        # otherwise wait until shift begins
        if not counsellor_shift.is_edge_case or (
            counsellor_shift.shift is Shifts.GRAVEYARD and 
            counsellor_shift.role is Roles.VOLUNTEER):
            yield self.env.timeout(counsellor_shift.start) # delay for counsellor_shift.start minutes
            shift_remaining = counsellor_shift.duration
        else:
            shift_remaining = counsellor_shift.end%MINUTES_PER_DAY
        

        while True:
            start_shift_time = self.env.now

            self.current_shift_start[counsellor_shift.shift] = start_shift_time
            self.current_shift_end[counsellor_shift.shift] = start_shift_time + shift_remaining

            for counsellor in self.counsellors[counsellor_shift.role][counsellor_shift.shift]:
                yield self.store_counsellors_active.put(counsellor)

                if start_shift_time > 0:
                    logging.debug(f'{Colors.GREEN}+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}')
                    logging.debug(f'{Colors.GREEN}Counsellor {counsellor.counsellor_id} signed in at t = {start_shift_time}({start_shift_time%MINUTES_PER_DAY:.3f}){Colors.WHITE}')
                    logging.debug(f'{Colors.GREEN}+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}\n')
                
                    # assert start_shift_time % MINUTES_PER_DAY == counsellor_shift.start or start_shift_time == 0
                    # assert counsellor in self.store_counsellors_active.items

            logging.debug(f'Signed in shift:{counsellor_shift.shift.name} at {start_shift_time}({int(((start_shift_time)%MINUTES_PER_DAY)//60)%24}).'
                f'  There are {len(self.store_counsellors_active.items)} idle SO counsellor processes:')
            self.log_idle_counsellors_working()

            if counsellor_shift.is_edge_case and counsellor_init:
                # deal with edge case one more time
                yield self.env.timeout(counsellor_shift.start)
                shift_remaining = counsellor_shift.duration
                counsellor_init = False
            else:
                # repeat every 24 hours
                yield self.env.timeout(MINUTES_PER_DAY) 

    #---------------------------------------------------------------------------

    def counsellors_signout(self, counsellor_shift):
        '''
            routine to sign out counsellors during a shift

            param:
            counsellor_shift - CounsellorShift DataClass
        '''

        total_procs = counsellor_shift.num_workers * counsellor_shift.role.num_processes

        # delay for shift.end minutes
        # taking the mod to deal with first initialized graveyard or special shifts (edge cases)
        if counsellor_shift.shift is Shifts.GRAVEYARD and\
            counsellor_shift.role is Roles.VOLUNTEER:
            yield self.env.timeout(counsellor_shift.end)
        else:
            yield self.env.timeout(counsellor_shift.end % MINUTES_PER_DAY)

        while True:
            counsellors_still_serving = set(self.counsellors[counsellor_shift.role][counsellor_shift.shift]).difference(
                set(self.store_counsellors_active.items) )
            if len(counsellors_still_serving) > 0:
                logging.debug(f'{Colors.BLUE}--------------INCOMPLETE {counsellor_shift.shift.name} {self.env.now} ({self.env.now%MINUTES_PER_DAY})--------------{Colors.WHITE}')
                # logging.debug([c.counsellor_id for c in self.counsellors[shift]])
                logging.debug([c.counsellor_id for c in counsellors_still_serving])
                self.log_idle_counsellors_working()

                try:
                    self.user_procs.interrupt((JobStates.SIGNOUT, counsellors_still_serving) ) # throw an interrupt
                    logging.debug(f'{Colors.RED}@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@{Colors.WHITE}')
                    logging.debug(f'@@@@ Interrupt user process handled by {counsellors_still_serving} @@@@')
                    logging.debug(f'{Colors.RED}@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@{Colors.WHITE}')
                except RuntimeError:
                    logging.debug(f'{Colors.BLUE}@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@{Colors.WHITE}')
                    logging.debug(f'@@@@ Cannot interrupt user process handled by {counsellors_still_serving} as it is already completed. @@@@')
                    logging.debug(f'{Colors.BLUE}@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@{Colors.WHITE}')
                
            total_procs_remaining = total_procs - len(counsellors_still_serving)
            counsellor_procs = [self.store_counsellors_active.get(
                lambda x: x.counsellor_shift.shift is counsellor_shift.shift and x.counsellor_shift.role is counsellor_shift.role)
                for _ in range(total_procs_remaining)]

            # wait for all procs
            counsellor = yield AllOf(self.env, counsellor_procs)
            counsellor_instances = [counsellor[list(counsellor)[i]] for i in range(total_procs_remaining)]

            end_shift_time = self.env.now

            for c in counsellor_instances:
                c.reset() # set break flags
                logging.debug(f'{Colors.RED}--------------------------------------------------------------------------{Colors.WHITE}')
                logging.debug(f'{Colors.RED}Counsellor {c.counsellor_id} signed out at t = {end_shift_time:.3f} ({end_shift_time%MINUTES_PER_DAY:.3f}).{Colors.WHITE}')
                logging.debug(f'{Colors.RED}--------------------------------------------------------------------------{Colors.WHITE}\n')
                # assert end_shift_time % MINUTES_PER_DAY == counsellor_shift.start or end_shift_time == 0
                # assert c not in self.store_counsellors_active.items            

            logging.debug(f'Signed out shift:{counsellor_shift.shift.name} at {end_shift_time}({int((int(end_shift_time)%MINUTES_PER_DAY)/60)%24}).'
                f'  There are {len(self.store_counsellors_active.items)} idle SO counsellor processes:\n')
            self.log_idle_counsellors_working()

            # repeat every 24 hours - overtime
            yield self.env.timeout(MINUTES_PER_DAY)

    #---------------------------------------------------------------------------

    def counsellors_break_start(self, counsellor_shift):
        '''
            routine to start a meal break

            param:
            counsellor_shift - CounsellorShift DataClass instance
        '''

        total_procs = counsellor_shift.num_workers * counsellor_shift.role.num_processes

        # delay until meal break starts
        yield self.env.timeout(counsellor_shift.meal_start)

        while True:

            counsellors_still_serving = set(self.counsellors[counsellor_shift.role][counsellor_shift.shift]).difference(
                set(self.store_counsellors_active.items) )
            if len(counsellors_still_serving) > 0:
                logging.debug(f'{Colors.BLUE}--------------INCOMPLETE {counsellor_shift.shift.name} {self.env.now} ({self.env.now%MINUTES_PER_DAY})--------------{Colors.WHITE}')
                # logging.debug([c.counsellor_id for c in self.counsellors[counsellor_shift.shift]])
                logging.debug([c.counsellor_id for c in counsellors_still_serving])
                self.log_idle_counsellors_working()

                try:
                    self.user_procs.interrupt((JobStates.MEAL_BREAK, counsellors_still_serving) ) # throw an interrupt
                    logging.debug(f'{Colors.RED}@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@{Colors.WHITE}')
                    logging.debug(f'@@@@ Interrupt user process handled by {counsellors_still_serving} @@@@')
                    logging.debug(f'{Colors.RED}@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@{Colors.WHITE}')
                except RuntimeError:
                    logging.debug(f'{Colors.BLUE}@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@{Colors.WHITE}')
                    logging.debug(f'@@@@ Cannot interrupt user process handled by {counsellors_still_serving} as it is already completed. @@@@')
                    logging.debug(f'{Colors.BLUE}@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@{Colors.WHITE}')
                
            total_procs_remaining = total_procs - len(counsellors_still_serving)
            counsellor_procs = [self.store_counsellors_active.get(
                lambda x: x.counsellor_shift.shift is counsellor_shift.shift and x.counsellor_shift.role is counsellor_shift.role)
                for _ in range(total_procs_remaining)]

            # wait for all procs
            counsellor = yield AllOf(self.env, counsellor_procs)
            counsellor_instances = [counsellor[list(counsellor)[i]] for i in range(total_procs_remaining)]

            break_init_time = self.env.now

            for c in counsellor_instances:
                c.reset() # set break flags
                logging.debug(f'{Colors.BLUE}**************************************************************************{Colors.WHITE}')
                logging.debug(f'{Colors.BLUE}Counsellor {c.counsellor_id} AFK at t = {break_init_time:.3f} ({break_init_time%MINUTES_PER_DAY:.3f}).{Colors.WHITE}')
                logging.debug(f'{Colors.BLUE}**************************************************************************{Colors.WHITE}\n')

                # assert end_shift_time % MINUTES_PER_DAY == shift.start or end_shift_time == 0
                # assert c not in self.store_counsellors_active.items            

            logging.debug(f'Shift {counsellor_shift.shift.name} taking meal break at {break_init_time}({int((int(break_init_time)%MINUTES_PER_DAY)/60)%24}).'
                f'  There are {len(self.store_counsellors_active.items)} idle SO counsellor processes:\n')
            self.log_idle_counsellors_working()

            # repeat every 24 hours
            yield self.env.timeout(MINUTES_PER_DAY)

    #---------------------------------------------------------------------------

    def counsellors_break_end(self, counsellor_shift):
        '''
            handle to end the meal_break

            param:
            counsellor_shift - CounsellorShift DataClass instance
        '''

        # delay until meal break starts
        yield self.env.timeout(counsellor_shift.meal_start + self.__meal_break)

        while True:
            end_break_time = self.env.now

            for counsellor in self.counsellors[counsellor_shift.role][counsellor_shift.shift]:
                yield self.store_counsellors_active.put(counsellor)

                logging.debug(f'{Colors.BLUE}##########################################################################{Colors.WHITE}')
                logging.debug(f'{Colors.BLUE}Counsellor {counsellor.counsellor_id} BAK at t = {end_break_time}({end_break_time%MINUTES_PER_DAY:.3f}){Colors.WHITE}')
                logging.debug(f'{Colors.BLUE}##########################################################################{Colors.WHITE}\n')
                
                # assert start_shift_time % MINUTES_PER_DAY == counsellor_shift.start or start_shift_time == 0
                # assert counsellor in self.store_counsellors_active.items

            logging.debug(f'Shift {counsellor_shift.shift.name} resumed at {end_break_time}({int(((end_break_time)%MINUTES_PER_DAY)//60)%24}).'
                f'  There are {len(self.store_counsellors_active.items)} idle SO counsellor processes:')
            self.log_idle_counsellors_working()

            # repeat every 24 hours
            yield self.env.timeout(MINUTES_PER_DAY) 

    ############################################################################
    # user related functions
    ############################################################################

    def create_users(self):
        '''
            function to generate users in the background
            at "interarrival_time" invervals to mimic user interarrivals
        '''

        for i in itertools.count(): # use this instead of while loop 
                                    # for efficient looping
                                    
            # space out incoming users
            # logging.debug(self.env.active_process)
            interarrival_time = self.assign_interarrival_time(i)
            if interarrival_time is None:
                continue # skip the rest of the code and move to next iteration

            start_time = self.env.now

            while interarrival_time:
                try:
                    yield self.env.timeout(interarrival_time)
                    interarrival_time = 0

                except simpy.Interrupt as si:
                    # find the job
                    if isinstance(si.cause, tuple) and si.cause[0] in [JobStates.SIGNOUT, JobStates.MEAL_BREAK]:
                        counsellors_to_sign_out = si.cause[-1]
                        cause = si.cause[0]

                        for c in counsellors_to_sign_out:
                            if c.client_id is not None:
                                try:
                                    self.user_handler[c.client_id%LEN_CIRCULAR_ARRAY].interrupt((cause, c) )
                                except RuntimeError:
                                    logging.debug(f'{Colors.BLUE}**************************************************************************{Colors.HEND}')
                                    logging.debug(f'{Colors.BLUE}User {c.client_id} process cannot be interrupted{Colors.HEND}')
                                    logging.debug(f'{Colors.BLUE}**************************************************************************{Colors.HEND}\n')

                    interarrival_time -= self.env.now - start_time # reset timeout
                    interarrival_time = max(0, interarrival_time) # make sure interarrival_time >=0


            self.num_users += 1 # increment counter
            uid = self.num_users + 1

            # if TOS accepted, send add user to the queue
            # otherwise increment counter and do nothing
            tos_state = self.assign_TOS_acceptance()
            if tos_state == TOS.TOS_ACCEPTED:
                self.num_users_TOS_accepted += 1
                self.user_handler[uid%LEN_CIRCULAR_ARRAY] = self.env.process(
                    self.handle_user(uid)
                )

                logging.debug(f'{Colors.GREEN}**************************************************************************{Colors.HEND}')
                logging.debug(f'{Colors.GREEN}User {uid} has just accepted TOS.  Chat session created at '
                    f'{self.env.now:.3f}{Colors.HEND}')
                logging.debug(f'{Colors.GREEN}**************************************************************************{Colors.HEND}\n')

            else: # if TOS.TOS_REJECTED
                self.num_users_TOS_rejected += 1

                logging.debug(f'{Colors.BLUE}**************************************************************************{Colors.HEND}')
                logging.debug(f'{Colors.BLUE}User {uid} rejected TOS at {self.env.now}{Colors.HEND}')
                logging.debug(f'{Colors.BLUE}**************************************************************************{Colors.HEND}\n')

        # otherwise, do nothing
    #---------------------------------------------------------------------------

    def handle_user(self, user_id):

        '''
            user process handler

            this function deals with "wait", "renege", and "chat" states
            in the user state diagram

            param:
                user_id - user id (integer)
        '''

        # lambda filters
        def case_cutoff(x):
            '''
                lambda filter for case cutoff (limiting overtime)
                Conditionals make sure edge cases 
                (Special and Graveyard) are being dealt with
            '''
            current_time = self.env.now

            shift_end = self.current_shift_end.get(x.counsellor_shift.shift)
            if shift_end is not None:
                diff = shift_end - current_time
            else:
                diff = -current_time
            return diff > LAST_CASE_CUTOFF


        def get_counsellor(x, risk):
            '''
                lambda filter for get_counsellor
            '''
            if risk in [Risklevels.HIGH, Risklevels.CRISIS]:
                return x.counsellor_shift.role in [
                    r for r in Roles if r is not Roles.VOLUNTEER]

            return x.counsellor_shift.role in [
                r for r in Roles if r is not Roles.DUTY_OFFICER]


        user_status = self.assign_user_status()
        risklevel = self.assign_risklevel(user_status)
        renege_time = self.assign_renege_time(user_status.mean_patience)
        # renege_time = self.assign_renege_time(
        #     user_status.alpha_renege_time,
        #     user_status.beta_renege_time,
        #     user_status.shape_renege_time,
        #     user_status.loc_renege_time)

        if user_status is Users.REPEATED:
            chat_duration = self.assign_chat_duration(
                risklevel.alpha_repeated_user,
                risklevel.beta_repeated_user,
                risklevel.shape_repeated_user
            )
        else:
            chat_duration = self.assign_chat_duration(
                risklevel.alpha_non_repeated_user,
                risklevel.beta_non_repeated_user,
                risklevel.shape_non_repeated_user
            )

        transfer_case = False # if process is interrupted, this flag is set to True
        self.users_in_system.append(user_id)
        self.user_queue.append(user_id)
        cumulative_chat_time = 0
        user_reneged = False # if user reneged, this is set to True


        while chat_duration:
            start_time = self.env.now

            # wait for a counsellor matching role or renege
            # get only counsellors matching risklevel to role
            # and remaining shift > LAST_CASE_CUTOFF
            counsellor = self.store_counsellors_active.get(
                lambda x: case_cutoff(x) and get_counsellor(x, risklevel)
            )

            results = yield counsellor | self.env.timeout(renege_time)
            


            # record the time spent in the queue
            current_time = self.env.now
            time_spent_in_queue = current_time - start_time
            current_day_minutes = int(current_time) % MINUTES_PER_DAY
            weekday = int(current_time / MINUTES_PER_DAY) % DAYS_IN_WEEK
            hour = int(current_day_minutes / MINUTES_PER_HOUR)
            if counsellor in results:
                if not transfer_case:
                    self.queue_time_stats.append({
                        'weekday': weekday,
                        'hour': hour,
                        'time_spent_in_queue': time_spent_in_queue,
                    })
                else:
                    self.queue_time_stats_transfer.append({
                        'weekday': weekday,
                        'hour': hour,
                        'time_spent_in_queue': time_spent_in_queue,
                    })

            else: # if user has reneged
                if not transfer_case:
                    self.renege_time_stats.append({
                        'weekday': weekday,
                        'hour': hour,
                        'time_spent_in_queue': renege_time,
                    })
                else:
                    self.renege_time_stats_transfer.append({
                        'weekday': weekday,
                        'hour': hour,
                        'time_spent_in_queue': renege_time,
                    })




            # dequeue user in the waiting queue
            self.user_queue.remove(user_id) 
            current_user_queue_length = len(self.user_queue)

            # update maximum user queue length
            if current_user_queue_length > self.user_queue_max_length:
                self.user_queue_max_length = current_user_queue_length

                logging.debug(f'Updated max queue length to '
                    f'{self.user_queue_max_length}.\n'
                    f'User Queue: {self.user_queue}\n\n\n')

            # update queue status
            if current_user_queue_length >= QUEUE_THRESHOLD:
                current_time = self.env.now
                current_day_minutes = int(current_time) % MINUTES_PER_DAY
                weekday = int(current_time / MINUTES_PER_DAY) % DAYS_IN_WEEK
                hour = int(current_day_minutes / MINUTES_PER_HOUR)

                logging.debug(
                    f'Weekday: {weekday} - '
                    f'Hour: {hour}, '
                    f'Queue Length: {current_user_queue_length}'
                )

                self.queue_status.append({
                    'weekday': weekday,
                    'hour': hour,
                    'queue_length': current_user_queue_length
                })

            logging.debug(f'Current User Queue contains: {self.user_queue}')



            # store number of available counsellor processes at current timestamp
            self.num_available_counsellor_processes.append(
                (self.env.now, len(self.store_counsellors_active.items) )
            )
                


            if counsellor not in results: # if user reneged
                counsellor.cancel() # cancel counsellor request
                chat_duration = 0

                # remove user from system record
                self.users_in_system.remove(user_id)
                if not transfer_case:
                    self.reneged += 1 # update counter
                    log_string = f'{Colors.HBLUE}No counsellor has picked up the case before user reneged.{Colors.HEND}'
                else:
                    self.reneged_during_transfer += 1
                    self.case_chat_time.append(cumulative_chat_time)
                    if cumulative_chat_time >= self.valid_chat_threshold:
                        self.served_g_valid += 1
                    log_string = f'{Colors.HBLUE}The session lasted {cumulative_chat_time:.3f} minutes.\n\n{Colors.HEND}'

                logging.debug(f'{Colors.HRED}**************************************************************************{Colors.HEND}')
                logging.debug(f'{Colors.HRED}User {user_id} reneged after '
                    f'spending t = {renege_time:.3f} minutes in the queue.{Colors.HEND}')
                logging.debug(log_string)
                logging.debug(f'{Colors.HRED}**************************************************************************{Colors.HEND}\n')

                logging.debug(f'Users in system: {self.users_in_system}')


                # wait for counsellor to enter chatroom and terminate case
                with self.store_counsellors_active.get(
                    lambda x: case_cutoff(x) and get_counsellor(x, risklevel)
                ) as counsellor:
                
                    counsellor_instance = yield counsellor # wait for counsellor to cancel case
                    counsellor_instance.client_id = user_id
                    
                    try:
                        # counsellor has to enter chatroom to cancel case
                        # and fill out counsellor postchat
                        if transfer_case:
                            yield self.env.timeout(self.__counsellor_postchat_survey_time_if_served)
                        else:
                            yield self.env.timeout(self.__counsellor_postchat_survey_time_if_reneged)
                        
                    except simpy.Interrupt as si:
                        if isinstance(si.cause, tuple) and si.cause[0] in [JobStates.SIGNOUT, JobStates.MEAL_BREAK]:
                            logging.debug(f'{Colors.YELLOW}**************************************************************************{Colors.HEND}')
                            logging.debug(f'{Colors.YELLOW}Counsellor {counsellor_instance.counsellor_id} left User {user_id}\'s\n'
                                f'reneged counselling session and {si.cause[0].status} at {self.env.now:.3f} ({self.env.now%MINUTES_PER_DAY:.3f}).\n{Colors.HEND}')
                            logging.debug(f'{Colors.YELLOW}**************************************************************************{Colors.HEND}\n')
                            counsellor_instance.client_id = None
                    
                    else:
                        # counsellor resource is now available
                        counsellor_instance.client_id = None
                        yield self.store_counsellors_active.put(counsellor_instance)                      
                

            else: # if counsellor takes in a user
                chat_start_time = self.env.now
                counsellor_instance = results[list(results)[0]] # unpack the counsellor instance
                counsellor_instance.client_id = user_id


                logging.debug(f'{Colors.HGREEN}**************************************************************************{Colors.HEND}')
                logging.debug(f'{Colors.HGREEN}User {user_id} is assigned to '
                    f'{counsellor_instance.counsellor_id} at {chat_start_time:.3f}{Colors.HEND}')
                logging.debug(f'{Colors.HGREEN}**************************************************************************{Colors.HEND}\n')


                if not transfer_case:  
                    self.served += 1 # update counter
                    if user_status is Users.REPEATED:
                        self.served_g_repeated += 1
                    else:
                        self.served_g_regular += 1


                chat_complete = None # flag to determine if chat is complete before postchat survey 
                try:
                    # timeout is chat duration + self.__counsellor_postchat_survey_time_if_served 
                    # minutes to fill out postchat survey
                    chat = yield self.env.timeout(chat_duration)
                    chat_complete = True
                    fill_postchat = yield self.env.timeout(
                        self.__counsellor_postchat_survey_time_if_served,
                        value=self.env.now
                    ) # if triggered, store timestamp
                    

                except simpy.Interrupt as si:
                    if isinstance(si.cause, tuple) and si.cause[0] in [JobStates.SIGNOUT, JobStates.MEAL_BREAK]:

                        counsellor_to_sign_out = si.cause[-1]
                        if counsellor_instance is counsellor_to_sign_out:
                            counsellor_instance.client_id = None # so user process will not be interrupted again

                            if chat_complete is True:
                                elapsed = chat_duration
                                chat_duration = 0
                            else: # still working on the chat, haven't started on the postchat form
                                elapsed = self.env.now - chat_start_time
                                chat_duration -= elapsed
                                chat_duration = max(0, chat_duration) # make sure not below 0. Fixes overflow and underflow problems
                            
                            cumulative_chat_time += elapsed


                            if chat_duration > 0: 
                                transfer_case = True # attempt to transfer case
                                self.user_queue.append(user_id) # put user back into queue
                                log_string = f'{Colors.HBLUE}Transferring User {user_id} to another counsellor.{Colors.HEND}. Remaining: {chat_duration:.3f}.  cumulative_chat_time: {cumulative_chat_time}'

                            else:
                                # remove user from system record
                                self.users_in_system.remove(user_id)
                                logging.debug(f'Users in system: {self.users_in_system}')

                                self.case_chat_time.append(cumulative_chat_time)
                                if cumulative_chat_time >= self.valid_chat_threshold:
                                    self.served_g_valid += 1

                                # chat_duration = 0
                                log_string = f'{Colors.HBLUE}The session lasted {cumulative_chat_time:.3f} minutes.\n\n{Colors.HEND}'


                            logging.debug(f'{Colors.HBLUE}**************************************************************************{Colors.HEND}')
                            logging.debug(f'{Colors.HBLUE}Counsellor {counsellor_instance.counsellor_id} left User {user_id}\'s\n'
                                f'counselling session and {si.cause[0].status} at {self.env.now:.3f} ({self.env.now%MINUTES_PER_DAY:.3f}).\n{Colors.HEND}')
                            logging.debug(log_string)
                            logging.debug(f'{Colors.HBLUE}**************************************************************************{Colors.HEND}\n')

                
                else:
                    # put the counsellor back into the store, so it will be available
                    # to the next user
                    try:
                        elapsed = fill_postchat - chat_start_time # exclude time spent on postchat survey (up to fill_postchat trigger time)
                        assert elapsed > 0
                    except AssertionError:
                        elapsed = 0

                    cumulative_chat_time += elapsed

                    self.case_chat_time.append(cumulative_chat_time)
                    if cumulative_chat_time >= self.valid_chat_threshold:
                        self.served_g_valid += 1

                    logging.debug(f'{Colors.HBLUE}**************************************************************************{Colors.HEND}')
                    logging.debug(f'{Colors.HBLUE}User {user_id}\'s counselling session lasted t = '
                        f'{cumulative_chat_time:.3f} minutes.\nCounsellor {counsellor_instance.counsellor_id} '
                        f'is now available at {self.env.now:.3f}.{Colors.HEND}')
                    logging.debug(f'{Colors.HBLUE}**************************************************************************{Colors.HEND}\n')


                    # remove user from system record
                    self.users_in_system.remove(user_id)
                    logging.debug(f'Users in system: {self.users_in_system}')

                    # counsellor resource is now available
                    yield self.store_counsellors_active.put(counsellor_instance)

                    chat_duration = 0

    ############################################################################
    # Predefined Distribution Getters
    ############################################################################

    def assign_interarrival_time(self, idx=None):
        '''
            Getter to assign interarrival time using Thinning Algorithm on an
            interval-interval basis
            
            interarrival time follows the exponential distribution

            returns - interarrival time
        '''

        if idx is not None and self.interarrivals is not None:
            end_interarrivals = len(self.interarrivals)
            return self.interarrivals[idx%end_interarrivals]




        def get_max_arrival_rate(start_interval, end_interval):
            '''
                helper function to get the arrival rate lambda at within an
                interval range

                param:  start_interval - index to start
                        end_interval - index to end

                precondition: start_interval >= end_interval
            '''

            # take the maximum arrival rate within interval
            arrival_rate = self.time_series.predict(
                start=start_interval+OFFSET, end=end_interval+OFFSET).max()
            if self.boxcox_lambda is not None:
                arrival_rate = inv_boxcox(
                    arrival_rate, self.boxcox_lambda)
            return arrival_rate

        #-----------------------------------------------------------------------

        def get_arrival_rate(interval):
            '''
                helper function to get the arrival rate lambda at the specific
                interval

                param:  interval - index
            '''

            return get_max_arrival_rate(interval+OFFSET, interval+OFFSET)

        #-----------------------------------------------------------------------


        # cast this as integer to get a rough estimate
        # calculate the nearest hour as an integer
        # use it to access the mean interarrival time, from which the lambda
        # can be calculated
        current_time = self.env.now
        current_time_int = int(current_time)
        current_weekday = int(current_time_int / MINUTES_PER_DAY)
        current_day_minutes = current_time % MINUTES_PER_DAY
        nearest_two_hours = int(current_day_minutes / 120)

        local_max_idx_pt = int(self.time_series_period * current_weekday + nearest_two_hours)
        max_idx_start = local_max_idx_pt - 1
        max_idx_end = local_max_idx_pt + 1# self.time_series_period - 1
        if max_idx_end > 1104:
            max_idx_end = 1104

        # generate the dominant homogeneous Poisson Process
        max_arrival_rate = get_max_arrival_rate(max_idx_start, max_idx_end)
        homo_interarrival_time = random.expovariate(max_arrival_rate)
        return homo_interarrival_time


        # find idx = x+t
        next_arrival_time = current_time + homo_interarrival_time
        next_weekday = int(next_arrival_time / MINUTES_PER_DAY)
        next_arrival_time_day_minutes = next_arrival_time % MINUTES_PER_DAY
        next_nearest_two_hour_interval = int(next_arrival_time_day_minutes / 120)
        idx = int(self.time_series_period*next_weekday + next_nearest_two_hour_interval)
    
        # calculate lambda(x+t)
        next_arrival_rate = get_arrival_rate(idx)

        # logging.debug(f'Current time: {current_time}')
        # logging.debug(f'Current weekday: {current_weekday}')

        # logging.debug(f'Next time: {next_arrival_time}')
        # logging.debug(f'Next weekday: {next_weekday}')
        # logging.debug(f'Next Minutes day: {next_arrival_time_day_minutes}')
        # logging.debug(f'Next Nearest two hour: {next_nearest_two_hour_interval}')
        # logging.debug(f'index: {idx}')


        # decide whether to output interarrival time
        random_num = self.thinning_random.uniform(0, 1)
        if random_num <= (next_arrival_rate / max_arrival_rate):
            return homo_interarrival_time
        return None

    #---------------------------------------------------------------------------

    def assign_renege_time(self, mean_patience):
        '''
            Getter to assign patience to user
            user patience follows a beta distribution

            param:  mean_patience - mean patience
                    beta - beta shape parameter
                    scale - scale to apply to the standardized beta distribution

            returns - renege time
        '''
        renege_time = random.expovariate(1/mean_patience)
        if renege_time <= 0:
            return 0.1
        return renege_time

    # def assign_renege_time(self, alpha, beta, scale, loc):
    #     '''
    #         Getter to assign patience to user
    #         user patience follows a beta distribution

    #         param:  alpha - alpha shape parameter
    #                 beta - beta shape parameter
    #                 scale - scale to apply to the standardized beta distribution

    #         returns - renege time
    #     '''
    #     renege_time = betavariate.rvs(alpha, beta, loc=loc, scale=scale)
    #     if renege_time <= 0:
    #         return 0.1
    #     return renege_time

    #---------------------------------------------------------------------------

    def assign_chat_duration(self, alpha, beta, scale, loc=0):
        '''
            Getter to assign chat duration
            chat duration follows the beta distribution
            with alpha and beta values derived from the OpenUp MCCIS data

            param:  alpha - alpha shape parameter
                    beta - beta shape parameter
                    scale - scale to apply to the standardized beta distribution

            returns - chat duration or MAX_CHAT_DURATION if chat time has exceeded
                service standards
        '''
        
        duration = betavariate.rvs(alpha, beta, loc=loc, scale=scale)
        if duration <= 0:
            return 0.1
        elif duration < MAX_CHAT_DURATION:
            return duration
        # otherwise
        return MAX_CHAT_DURATION

    #---------------------------------------------------------------------------

    def assign_risklevel(self, user_type):
        '''
            Getter to assign risklevels

            param: user_type - one of either Users enum
        '''
        options = list(Risklevels)
        probability = [x.value[user_type.index][0] for x in options]

        return random.choices(options, probability)[0]

    #---------------------------------------------------------------------------

    def assign_user_status(self):
        '''
            Getter to assign user status
        '''
        options = list(Users)
        probability = [x.value[-1][0] for x in options]

        return random.choices(options, probability)[0]

    #---------------------------------------------------------------------------

    def assign_TOS_acceptance(self):
        '''
            Getter to assign TOS status
        '''
        
        options = list(TOS)
        probability = [x.value[-1] for x in options]

        return random.choices(options, probability)[0]

    ############################################################################
    # File IO functions
    ############################################################################

    def read_interarrival_time(self):
        '''
            file input function to read in actual interarrivals file      
        '''
        try:
            with open(NOV_INTERARRIVALS, 'r') as f:
                interarrivals = [float(i) for i in f.readlines() ]

            return interarrivals

        except Exception as e:
            print('Unable to read interarrivals file.')

    ############################################################################
    # Debugging functions
    ############################################################################

    def log_idle_counsellors_working(self):
        logging.debug(f'{Colors.YELLOW}##################################{Colors.WHITE}')
        logging.debug(f'{Colors.YELLOW}Items in Active Counsellors List:{Colors.WHITE}')
        logging.debug(f'{Colors.YELLOW}##################################{Colors.WHITE}')
        for x in self.store_counsellors_active.items:
            logging.debug(f'{Colors.YELLOW}{x.counsellor_id}{Colors.WHITE}')
        logging.debug('\n\n\n')         

#--------------------------------------------------end of ServiceOperation class

################################################################################
# Main Function
################################################################################

def main():
    logging.debug('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    logging.debug('Initializing OpenUp Queue Simulation')
    logging.debug('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')

    # seed for thinning algorithm
    thinning_random = random.Random()
    thinning_random.seed(THINNING_SEED)

    # global random seed
    random.seed(SEED)
    np.random.seed(SEED)

    boxcox_lambda = .5 # transformation is sqrt(y)
    ts_period = 12
    num_harmonics = 3
    

    # load time series of interarrivals (specified in SECONDS)
    df = pd.read_csv(INTERARRIVALS_FILE, index_col=0)
    if df is None:
        return
    # otherwise
    transformed_data = boxcox(1/df['y'], boxcox_lambda)

    ucm = UnobservedComponents(
        transformed_data,
        level='fixed intercept',
        freq_seasonal=[
            {'period': ts_period,'harmonics': num_harmonics},
        ],
        autoregressive=1,
    )
    fitted_ts = ucm.fit(disp=False)


    # # create environment
    env = simpy.Environment()


    # volunteer shifts
    # from 8pm to 12am
    # from 10:30am to 2:30 pm
    # from 3pm to 7pm
    # from 6pm to 10pm
    volunteer_shifts = [
        CounsellorShift(Shifts.GRAVEYARD, Roles.VOLUNTEER, True, 1200, 1440, 1),
        CounsellorShift(Shifts.AM, Roles.VOLUNTEER, False, 630, 870, 1),
        CounsellorShift(Shifts.PM, Roles.VOLUNTEER, False, 900, 1140, 1),
        CounsellorShift(Shifts.SPECIAL, Roles.VOLUNTEER, False, 1080, 1320, 1),
    ]


    # duty officer and social worker shifts
    # from 9:30pm to 7:30am
    # from 7:15am to 3:15 pm
    # from 2pm to 10pm
    # from 5pm to 1 am
    duty_officer_shifts = [
        CounsellorShift(Shifts.GRAVEYARD, Roles.DUTY_OFFICER, True, 1290, 1890, 1),
        CounsellorShift(Shifts.AM, Roles.DUTY_OFFICER, False, 435, 915, 1),
        CounsellorShift(Shifts.PM, Roles.DUTY_OFFICER, False, 840, 1320, 1),
        CounsellorShift(Shifts.SPECIAL, Roles.DUTY_OFFICER, True, 1020, 1500, 0),
    ]
            

    social_worker_shifts = [
        CounsellorShift(Shifts.GRAVEYARD, Roles.SOCIAL_WORKER, True, 1290, 1890, 1),
        CounsellorShift(Shifts.GRAVEYARD, Roles.SOCIAL_WORKER2, True, 1290, 1890, 1),
        CounsellorShift(Shifts.AM, Roles.SOCIAL_WORKER, False, 435, 915, 1),
        CounsellorShift(Shifts.PM, Roles.SOCIAL_WORKER, False, 840, 1320, 1),
        CounsellorShift(Shifts.SPECIAL, Roles.SOCIAL_WORKER, True, 1020, 1500, 1),
    ]



    # set up service operation and run simulation until  
    S = ServiceOperation(env=env,
        volunteer_shifts=volunteer_shifts,
        duty_officer_shifts=duty_officer_shifts,
        social_worker_shifts=social_worker_shifts,
        ts=fitted_ts, ts_period=ts_period,
        thinning_random=thinning_random, boxcox_lambda=boxcox_lambda,
        use_actual_interarrivals=True, )
    env.run(until=SIMULATION_DURATION)
    # # logging.debug(S.assign_risklevel() )

    logging.debug('\n\n\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    logging.debug(f'Final Results ')#-- number of simultaneous chats: {MAX_NUM_SIMULTANEOUS_CHATS}')
    logging.debug('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')


    logging.debug(f'{Colors.HBLUE}Stage 1. TOS Acceptance{Colors.HEND}')
    try:
        percent_accepted_TOS = S.num_users_TOS_accepted/S.num_users * 100
        percent_rejected_TOS = 100 - percent_accepted_TOS
    except ZeroDivisionError:
        percent_accepted_TOS = 0
        percent_rejected_TOS = 0
    logging.debug(f'1. Total number of Users visited OpenUp: {S.num_users}')
    logging.debug(f'2. Total number of Users accepted TOS: {S.num_users_TOS_accepted} ({percent_accepted_TOS:.02f}% of (1) )')
    logging.debug(f'3. Total number of Users rejected TOS: {S.num_users_TOS_rejected} ({percent_rejected_TOS:.02f}% of (1) )\n')


    logging.debug(f'{Colors.HBLUE}Stage 2a. Number of users served given TOS acceptance{Colors.HEND}')
    try:
        percent_served = S.served/S.num_users_TOS_accepted * 100
        percent_served_repeated = S.served_g_repeated/S.served * 100
        percent_served_regular = S.served_g_regular/S.served * 100
        percent_served_valid = S.served_g_valid/S.served * 100
    except ZeroDivisionError:
        percent_served = 0
        percent_served_repeated = 0
        percent_served_regular = 0
        percent_served_valid = 0
    logging.debug(f'4. Total number of Users served: {S.served} ({percent_served:.02f}% of (2) )')
    logging.debug(f'5. Total number of Users served -- repeated user: {S.served_g_repeated} ({percent_served_repeated:.02f}% of (4) )')
    logging.debug(f'6. Total number of Users served -- user: {S.served_g_regular} ({percent_served_regular:.02f}% of (4) )')
    logging.debug(f'7. Total number of Users served -- cases above validation threshold: {S.served_g_valid} ({percent_served_regular:.02f}% of (4) )\n')

    logging.debug(f'{Colors.HBLUE}Stage 2b. Number of users reneged given TOS acceptance{Colors.HEND}')
    try:
        percent_reneged = S.reneged/S.num_users_TOS_accepted * 100
    except ZeroDivisionError:
        percent_reneged = 0
    logging.debug(f'8. Total number of Users reneged when assigned to the (first) counsellor: {S.reneged} ({percent_reneged:.02f}% of (2) )\n')
    logging.debug(f'9. Total number of Users reneged during a case transfer: {S.reneged_during_transfer}')


    logging.debug(f'{Colors.HBLUE}Queue Status{Colors.HEND}')
    logging.debug(f'10. Maximum user queue length: {S.user_queue_max_length}')
    logging.debug(f'11. Number of instances waiting queue is not empty after first person has been dequeued: {len(S.queue_status)}')
    # logging.debug(f'full last debriefing duration: {S.queue_status}')

if __name__ == '__main__':
    main()