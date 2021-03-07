'''
    This program uses Simpy to simulate the OpenUp Counselling Service 
    platform and user arrivals.  

    Users will renege when they loose patience waiting in the queue

    Non interrupt version - Counsellors are forced to work overtime, serving
    users before they can sign out.

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
# from scipy.stats import poisson
from simpy.events import AllOf
from statsmodels.tsa.statespace.structural import UnobservedComponents
from scipy.stats import boxcox
from scipy.special import inv_boxcox
import pandas as pd


logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
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
    'DUTY_OFFICER': 1,                      # Duty Officer can process max 1 chat
    'VOLUNTEER': 2,                         # Volunteer can process max 2 chat
}    

SEED = 728                                  # for seeding the sudo-random generator
THINNING_SEED = 305                         # for seeding the thing algo sudo-random generator
OFFSET = 372

MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR     # 1440 minutes per day
SIMULATION_DURATION = MINUTES_PER_DAY * 30  # currently given as num minutes 
                                            #     per day * num days in month

POSTCHAT_FILLOUT_TIME = 20                  # time to fill out counsellor postchat


# counsellor average chat no longer than 60 minutes
# meaning differences between types of 1/mean_chat_duration will be negligible
MEAN_CHAT_DURATION_COUNSELLOR = {
    'SOCIAL_WORKER': 51.4,                  # Social Worker - average 51.4 minutes
    'DUTY_OFFICER': 56.9,                   # Duty Officer - average 56.9 minutes
    'VOLUNTEER': 57.2,                      # Volunteer - average 57.2 minutes
}


CURRENT_SHIFT_START = {
    'GRAVEYARD': None,
    'AM': None,
    'PM': None,
    'SPECIAL': None
}

CURRENT_SHIFT_END = {
    'GRAVEYARD': None,
    'AM': None,
    'PM': None,
    'SPECIAL': None
}


TEA_BREAK_DURATION = 20                     # 20 minute tea break
MEAL_BREAK_DURATION = 60                    # 60 minute meal break
DEBRIEF_DURATION = 60                       # 60 minute debriefing session per day
TRAINING_DURATION = 480                     # 8 hour (480 minute) training session - once per month
LAST_CASE_CUTOFF = 30                       # do not assign any more cases 30 minutes before signoff

NUM_DUTY_OFFICERS = {
    'GRAVEYARD': 1,
    'AM': 1,
    'PM': 1,
    'SPECIAL': 0
}

NUM_SOCIAL_WORKERS = {
    'GRAVEYARD': 7,
    'AM': 7,
    'PM': 4,
    'SPECIAL': 4
}

NUM_VOLUNTEERS = {
    'GRAVEYARD': 5,
    'AM': 2,
    'PM': 4,
    'SPECIAL': 5
}

MAX_CHAT_DURATION = 60 * 11 # longest chat duration is 11 hours (from OpenUp 1.0)

VALIDATE_CHAT_THRESHOLD = 20                # time elapsed in minutes to have a pingpong>=4 

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

class DutyOfficerShifts(enum.Enum):
    '''
        different types of paid worker shifts
        shift start, end, and next shift offset in minutes
    '''

    GRAVEYARD = ('GRAVEYARD',   True, 1290, 1890, 840) # from 9:30pm to 7:30am
    AM =        ('AM',          False, 435, 915, 960)  # from 7:15am to 3:15 pm
    PM =        ('PM',          False, 840, 1320, 960) # from 2pm to 10pm
    SPECIAL =   ('SPECIAL',     True, 1020, 1500, 960) # from 5pm to 1 am

    def __init__(self, shift_name, is_edge_case, start, end, offset):

        self.shift_name = shift_name
        self.is_edge_case = is_edge_case
        self.start = start
        self.end = end
        self.offset = offset
        self.num_workers = NUM_DUTY_OFFICERS.get(shift_name)


    @property
    def duration(self):
        return int(self.end - self.start)  

#-------------------------------------------------------------------------------

class SocialWorkerShifts(enum.Enum):
    '''
        different types of paid worker shifts
        shift start, end, and next shift offset in minutes
    '''

    GRAVEYARD = ('GRAVEYARD',   True, 1290, 1890, 840)   # from 9:30pm to 7:30am
    AM =        ('AM',          False, 435, 915, 960)    # from 7:15am to 3:15 pm
    PM =        ('PM',          False, 840, 1320, 960)   # from 2pm to 10pm
    SPECIAL =   ('SPECIAL',     True, 1020, 1500, 960)   # from 5pm to 1 am

    def __init__(self, shift_name, is_edge_case, start, end, offset):

        self.shift_name = shift_name
        self.is_edge_case = is_edge_case
        self.start = start
        self.end = end
        self.offset = offset
        self.num_workers = NUM_SOCIAL_WORKERS.get(shift_name)

    @property
    def duration(self):
        return int(self.end - self.start)  
    
#-------------------------------------------------------------------------------

class VolunteerShifts(enum.Enum):
    '''
        different types of unpaid worker shifts
        shift start, end, and next shift offset in minutes
    '''

    GRAVEYARD = ('GRAVEYARD',   True, 1200, 1440, 1200)  # from 8pm to 12am
    AM =        ('AM',          False, 630, 870, 1200)   # from 10:30am to 2:30 pm
    PM =        ('PM',          False, 900, 1140, 1200)  # from 3pm to 7pm
    SPECIAL =   ('SPECIAL',     False, 1080, 1320, 1200)  # from 6pm to 10pm

    def __init__(self, shift_name, is_edge_case, start, end, offset):

        self.shift_name = shift_name
        self.is_edge_case = is_edge_case
        self.start = start
        self.end = end
        self.offset = offset
        self.num_workers = NUM_VOLUNTEERS.get(shift_name)

    @property
    def duration(self):
        return int(self.end - self.start)

#-------------------------------------------------------------------------------

class JobStates(enum.Enum):
    '''
        Counsellor in three states:
        counselling, eating lunch, and adhoc duties,
        each of which are given different priorities (must be integers)

        The higher the priority, the lower the value 
        (10 has higher priority than 20)
    '''

    SIGNOUT =       ('SIGN_OUT',        10)
    CHAT =          ('CHAT',            30)
    MEAL_BREAK =    ('MEAL_BREAK',      20)
    FIRST_TEA =     ('FIRST_TEA_BREAK', 20)
    LAST_TEA =      ('LAST_TEA_BREAK',  20)

    def __init__(self, job_name, priority):
        self.job_name = job_name
        self.priority = priority

#-------------------------------------------------------------------------------

class AdHocDuty(enum.Enum):
    '''
        different types of shifts
        shift start, end, and next shift offset in minutes
    '''

    MORNING =   ('MORNING',     600,    840)    # from 10am to 2pm
    AFTERNOON = ('AFTERNOON',   840,    1080)   # from 2pm to 6pm
    EVENING =   ('EVENING',     1080,   1320)   # from 6pm to 10pm    

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
        Distribution of LOW/MEDIUM/HIGH/CRISIS
    '''
    # nested tuple order - p, mean, variance
    # risk enum | risklevel | non-repeated data | repeated data
    CRISIS =    ('CRISIS',  ( .0,   96.3, 54.3 ), ( .0,   149.1, 121.2 ) )
    HIGH =      ('HIGH',    ( .002, 96.3, 54.3 ), ( .001, 149.1, 121.2 ) )
    MEDIUM =    ('MEDIUM',  ( .080, 74.7, 42.3 ), ( .087, 81.0, 63.8 ) )
    LOW =       ('LOW',     ( .918, 52.9, 37.0 ), ( .906, 59.7, 60.0 ) )

    def __init__(self, risk,
        non_repeated_user_data, repeated_user_data):
        self.risk = risk

        self.p_non_repeated_user = non_repeated_user_data[0]
        self.mean_chat_duration_non_repeated_user  = non_repeated_user_data[1]
        self.variance_chat_duration_non_repeated_user = non_repeated_user_data[2]

        self.p_repeated_user = repeated_user_data[0]
        self.mean_chat_duration_repeated_user  = repeated_user_data[1]
        self.variance_chat_duration_repeated_user = repeated_user_data[2]
        
#-------------------------------------------------------------------------------

class Users(enum.Enum):
    '''
        Distribution of Repeated Users - 78% regular / 22% repeated
        among the users accepting TOS
    '''
    # nested tuple order - p, mean, variance
    # user enum | user status | user index | probability, mean, sd
    REPEATED =      ('REPEATED_USER',       1,  (.22, 57.2, 39.0) )
    NON_REPEATED =  ('NONREPEATED_USER',    2,  (.78, 70.5, 65.8) )
    
    def __init__(self, user_type, index, user_data):
        self.user_type = user_type
        self.index = index # index to access Risklevel probability
        self.probability = user_data[0]
        self.mean_patience = user_data[1]
        self.variance_patience = user_data[2]

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

#-------------------------------------------------------------------------------

class Roles(enum.Enum):
    '''
        Counsellor Roles

        # TODO: add repeated/non-repeated user mean chat duration
    '''

    SOCIAL_WORKER = ('SOCIAL_WORKER',   True,   True, False)
    DUTY_OFFICER =  ('DUTY_OFFICER',    True,   True, False)
    VOLUNTEER =     ('VOLUNTEER',       False,  True, False)

    def __init__(self, counsellor_type, meal_break, 
        first_tea_break, last_tea_break):
        self.counsellor_type = counsellor_type
        self.num_processes = MAX_SIMULTANEOUS_CHATS.get(counsellor_type)
        self.mean_chat_duration = MEAN_CHAT_DURATION_COUNSELLOR.get(
            counsellor_type)
        self.meal_break = meal_break
        self.first_tea_break = first_tea_break
        self.last_tea_break = last_tea_break

################################################################################
# Classes
################################################################################

class Counsellor:
    '''
        Class to create counsellor instances

        each counsellor is assigned a role, an id, a shift, 
        and an adhoc duty shift (if available)
    '''

    def __init__(self, env, counsellor_id, shift, role):
        '''
            param:

            env - simpy environment instance
            counsellor_id - an assigned counsellor id (STRING)
            shift - counsellor shift (one of Shifts enum)
        '''

        self.env = env
        self.counsellor_id = counsellor_id
        
        self.shift = shift
        self.role = role

#--------------------------------------------------------end of Counsellor class

class ServiceOperation:
    '''
        Class to emulate OpenUp Service Operation with a limited number of 
        counsellors to handle user chat requests during different shifts

        Users have to request a counsellor to begin the counselling
        process
    '''

    def __init__(self, *, env, ts, ts_period, thinning_random,
        boxcox_lambda=None, 
        postchat_fillout_time=POSTCHAT_FILLOUT_TIME,
        valid_chat_threshold=VALIDATE_CHAT_THRESHOLD,
        use_actual_interarrivals=False):

        '''
            init function

            param:
                env - simpy environment

                ts - fitted time series model (a statsmodel object)

                ts_period - period in the specified time series (an integer)

                boxcox_lambda - the fitted lambda variable, if available
                    default is set to None

                postchat_fillout_time - Time alloted to complete the counsellor postchat
                    if not specified, defaults to POSTCHAT_FILLOUT_TIME

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

        self.__counsellor_postchat_survey = postchat_fillout_time



        # counters and flags (also see properties section)
        self.num_users = 0 # to be changed in create_users()
        self.num_users_TOS_accepted = 0
        self.num_users_TOS_rejected = 0


        self.reneged = 0
        self.served = 0
        self.served_g_repeated = 0
        self.served_g_regular = 0
        self.served_g_valid = 0
 
        self.user_in_system = []
        self.user_queue = []
        self.queue_status = []
        self.queue_time_stats = []
        self.renege_time_stats = []
        self.case_chat_time = []

        self.num_available_counsellor_processes = []

        self.user_queue_max_length = 0


        self.processes = {} # the main idle process
        

        # service operation is given an infinite counsellor intake capacity
        # to accomodate four counsellor shifts (see enum Shifts for details)
        self.store_counsellors_active = simpy.FilterStore(env)

        # create counsellors at different shifts
        self.counsellors = {}
        for s in SocialWorkerShifts:
            self.counsellors[s] = []
            self.create_counsellors(s, Roles.SOCIAL_WORKER)
        for s in DutyOfficerShifts:
            self.counsellors[s] = []
            self.create_counsellors(s, Roles.DUTY_OFFICER)
        for s in VolunteerShifts:
            self.counsellors[s] = []
            self.create_counsellors(s, Roles.VOLUNTEER)

        # logging.debug(f'Counsellors Arranged:\n{self.counsellors}')

        # set up idle processes
        self.processes[Roles.DUTY_OFFICER] = {s: self.env.process(
            self.counsellors_idle(s, Roles.DUTY_OFFICER) ) for s in DutyOfficerShifts}
        self.processes[Roles.SOCIAL_WORKER] = {s: self.env.process(
            self.counsellors_idle(s, Roles.SOCIAL_WORKER) ) for s in SocialWorkerShifts}
        self.processes[Roles.VOLUNTEER] = {s: self.env.process(
            self.counsellors_idle(s, Roles.VOLUNTEER) ) for s in VolunteerShifts}



        # generate users
        # this process will not be disrupted even when counsellors sign out
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

    def create_counsellors(self, shift, role):
        '''
            subroutine to create counsellors during a shift

            param:
            shift - one of Shifts/Volunteer Shifts enum
            role - role matching shift, a role enum

            precondition - shift must match with role
        '''            

        # signing in involves creating multiple counsellor processes
        for id_ in range(1, shift.num_workers+1):
            for subprocess_num in range(1, role.num_processes+1):
                counsellor_id = f'{shift.shift_name}_{role.counsellor_type}_{id_}_process_{subprocess_num}'
                self.counsellors[shift].append(
                    Counsellor(self.env, counsellor_id, shift, role)
            )
            
        # logging.debug(f'create_counsellors shift:{shift.shift_name}\n{self.counsellors[shift]}\n\n')

    #---------------------------------------------------------------------------

    def counsellors_idle(self, shift, role):
        '''
            routine to sign in counsellors during a shift

            param:
            shift - one of Shifts enum
            role - role matching shift, a role enum

            precondition: role must match with shift
        '''

        total_procs = shift.num_workers * role.num_processes
        counsellor_init = True # init flag
        counsellor_init_2 = True # init flag # 2
        actual_end_shift_time = 0
        scheduled_end_shift_time = 0
        actual_end_break_time = 0

        if not shift.is_edge_case:
            yield self.env.timeout(shift.start) # wait until start of shift to begin shift

        while True:
            # start shift immediately if graveyard or special shift to account for edge case
            # otherwise wait until shift begins
            if shift.is_edge_case and counsellor_init:
                shift_remaining = shift.end%MINUTES_PER_DAY
                counsellor_init = False
            else:
                shift_remaining = shift.duration
                    


            while shift_remaining > 0:
                start_shift_time = self.env.now

                if shift.is_edge_case and counsellor_init_2:
                    scheduled_end_shift_time = start_shift_time + shift_remaining
                    counsellor_init_2 = False

                else:
                    scheduled_end_shift_time = start_shift_time + shift.duration


                if shift_remaining == shift.duration or shift_remaining == shift.end%MINUTES_PER_DAY:
                    CURRENT_SHIFT_START[shift.shift_name] = start_shift_time
                    CURRENT_SHIFT_END[shift.shift_name] = scheduled_end_shift_time

                        # begin shift by putting counsellors in the store
                    for counsellor in self.counsellors[shift]:
                        logging.debug(f'\n{Colors.GREEN}++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}')
                        logging.debug(f'{Colors.GREEN}Counsellor {counsellor.counsellor_id} signed in at t = {start_shift_time:.3f}{Colors.WHITE}')
                        logging.debug(f'{Colors.GREEN}++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}\n')
                        # assert start_shift_time % MINUTES_PER_DAY == shift.start or start_shift_time == 0
                        assert counsellor not in self.store_counsellors_active.items
                        yield self.store_counsellors_active.put(counsellor)

                    # logging.debug(f'Signed in shift:{shift.shift_name} at {start_shift_time}.'
                    #     f'  There are {len(self.store_counsellors_active.items)} idle SO counsellor processes:')
                    # self.print_idle_counsellors_working()

                yield self.env.timeout(shift_remaining) # delay for shift.start minutes
                    


                # allow only counsellors at a role and a shift to sign out
                counsellor_procs = [self.store_counsellors_active.get(
                    lambda x: x.shift is shift and x.role is role)
                    for _ in range(total_procs)]

                # wait for all procs
                counsellor = yield AllOf(self.env, counsellor_procs)

                # get all nested counsellor instances
                counsellor_instances = [counsellor[list(counsellor)[i]] for i in range(total_procs)]

                actual_end_shift_time = self.env.now
                CURRENT_SHIFT_START[shift.shift_name] = None
                CURRENT_SHIFT_END[shift.shift_name] = None
                for c in counsellor_instances:
                    logging.debug(f'\n{Colors.RED}--------------------------------------------------------------------------{Colors.WHITE}')
                    logging.debug(f'{Colors.RED}Counsellor {c.counsellor_id} signed out at t = {actual_end_shift_time:.3f}.  Overtime: {(actual_end_shift_time-scheduled_end_shift_time):.3f} minutes{Colors.WHITE}')
                    logging.debug(f'{Colors.RED}--------------------------------------------------------------------------{Colors.WHITE}\n')
                    # assert time_now % MINUTES_PER_DAY == shift.start or time_now == 0
                    assert counsellor not in self.store_counsellors_active.items

                logging.debug(f'Signed out shift:{shift.shift_name} at {self.env.now}.'
                    f'  There are {len(self.store_counsellors_active.items)} idle SO counsellor processes:')
                # self.print_idle_counsellors_working()

                shift_remaining = 0 # exit loop



            # this will deal with situation when shift actually ends during break
            max_end_shift_time = max(actual_end_break_time, actual_end_shift_time)
            overtime = max_end_shift_time - scheduled_end_shift_time
            next_offset = shift.offset - overtime
            logging.debug(f'Overtime: {overtime}, actual end shift {max_end_shift_time}, scheduled end shift {scheduled_end_shift_time} ')
            logging.debug(f'Next shift offset: {next_offset}, actual: {shift.offset}')
            
            # wait offset minutes - overtime for next shift
            # this fixes the edge case when counsellor goes overtime and
            # the next shift begins from overtime + offset
            yield self.env.timeout(next_offset)

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
            interarrival_time = self.assign_interarrival_time(i)
            if interarrival_time is None:
                continue # skip the rest of the code and move to next iteration

            yield self.env.timeout(interarrival_time)

            self.num_users += 1 # increment counter
            uid = self.num_users + 1

            # if TOS accepted, send add user to the queue
            # otherwise increment counter and do nothing
            tos_state = self.assign_TOS_acceptance()
            if tos_state == TOS.TOS_ACCEPTED:
                self.num_users_TOS_accepted += 1
                self.env.process(self.handle_user(uid) )
            else: # if TOS.TOS_REJECTED
                self.num_users_TOS_rejected += 1

    #---------------------------------------------------------------------------

    def handle_user(self, user_id):

        '''
            user process handler

            this function deals with "wait", "renege", and "chat" states
            in the user state diagram

            param:
                user_id - user id
        '''

        # lambda filters
        def case_cutoff(x):
            '''
                lambda filter for case cutoff (limiting overtime)
                Conditionals make sure edge cases 
                (Special and Graveyard) are being dealt with
            '''
            current_time = self.env.now

            shift_end = CURRENT_SHIFT_END.get(x.shift.shift_name)
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
                if x.shift.shift_name == 'GRAVEYARD':
                     return x.role in [Roles.DUTY_OFFICER, Roles.SOCIAL_WORKER]
                # otherwise
                return x.role is Roles.DUTY_OFFICER
            # else:
            return x.role in [Roles.SOCIAL_WORKER, Roles.VOLUNTEER]


        user_status = self.assign_user_status()
        risklevel = self.assign_risklevel(user_status)
        renege_time = self.assign_renege_time(
            user_status.mean_patience, user_status.variance_patience)

        if user_status is Users.REPEATED:
            chat_duration = self.assign_chat_duration(
                risklevel.mean_chat_duration_repeated_user,
                risklevel.variance_chat_duration_repeated_user
            )
        else:
            chat_duration = self.assign_chat_duration(
                risklevel.mean_chat_duration_non_repeated_user,
                risklevel.variance_chat_duration_non_repeated_user
            )

        process_user = chat_duration + self.__counsellor_postchat_survey # total time to process user
        init_flag = True


        while process_user:
            start_time = self.env.now

            if init_flag:
                logging.debug(f'\n{Colors.HGREEN}**************************************************************************{Colors.HEND}')
                logging.debug(f'{Colors.HGREEN}User -- {user_id} has just accepted TOS.  Chat session created at '
                        f'{start_time:.3f}{Colors.HEND}')
                logging.debug(f'{Colors.HGREEN}**************************************************************************{Colors.HEND}\n')

                self.user_in_system.append(user_id)
                self.user_queue.append(user_id)


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
                self.queue_time_stats.append({
                    'weekday': weekday,
                    'hour': hour,
                    'time_spent_in_queue': time_spent_in_queue,
                })
            else:
                self.renege_time_stats.append({
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


            # store number of available counsellor processes at time
            self.num_available_counsellor_processes.append(
                (self.env.now, len(self.store_counsellors_active.items) )
            )
            


            if counsellor not in results: # if user reneged
                # remove user from system record
                self.user_in_system.remove(user_id)
                logging.debug(f'User in system: {self.user_in_system}')
                time_spent_in_queue = renege_time

                logging.debug(f'\n{Colors.HRED}**************************************************************************{Colors.HEND}')
                logging.debug(f'{Colors.HRED}User {user_id} reneged after '
                    f'spending t = {renege_time:.3f} minutes in the queue.{Colors.HEND}')
                logging.debug(f'{Colors.HRED}**************************************************************************{Colors.HEND}\n')
                self.reneged += 1 # update counter
                counsellor.cancel() # cancel counsellor request
                process_user = 0
                init_flag = False



            else: # if counsellor takes in a user
                start_time = self.env.now
                counsellor_instance = results[list(results)[0]] # unpack the counsellor instance

                try:
                    logging.debug(f'\n{Colors.HGREEN}**************************************************************************{Colors.HEND}')
                    logging.debug(f'{Colors.HGREEN}User {user_id} is assigned to '
                        f'{counsellor_instance.counsellor_id} at {self.env.now:.3f}{Colors.HEND}')
                    logging.debug(f'{Colors.HGREEN}**************************************************************************{Colors.HEND}\n')

                    # timeout is chat duration + self.__counsellor_postchat_survey_time
                    # minutes to fill out postchat survey
                    yield self.env.timeout(process_user)

                    # put the counsellor back into the store, so it will be available
                    # to the next user
                    logging.debug(f'\n{Colors.HBLUE}**************************************************************************{Colors.HEND}')
                    logging.debug(f'{Colors.HBLUE}User {user_id}\'s counselling session lasted t = '
                        f'{chat_duration:.3f} minutes.\nCounsellor {counsellor_instance.counsellor_id} '
                        f'is now available at {self.env.now:.3f}.{Colors.HEND}')
                    logging.debug(f'{Colors.HBLUE}**************************************************************************{Colors.HEND}\n')


                    # remove user from system record
                    self.user_in_system.remove(user_id)
                    # logging.debug(f'User in system: {self.user_in_system}')

                    # counsellor resource is now available
                    yield self.store_counsellors_active.put(counsellor_instance)

                    self.served += 1 # update counter
                    if user_status is Users.REPEATED:
                        self.served_g_repeated += 1
                    else:
                        self.served_g_regular += 1

                    case_chat_time = process_user - self.__counsellor_postchat_survey
                    self.case_chat_time.append(case_chat_time)
                    if case_chat_time >= self.valid_chat_threshold:
                        self.served_g_valid += 1
                    process_user = 0


                except simpy.Interrupt as si:
                    process_user -= self.env.now - start_time
                    init_flag = False

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

        if idx is not None:
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

    def assign_renege_time(self, mean_patience, variance_patience):
        '''
            Getter to assign patience to user
            user patience follows the gamma distribution

            The python gamma pdf is parametrized with a shape parameter 
            alpha (k) and a scale parameter beta (theta)

            For details please see
            https://github.com/python/cpython/blob/master/Lib/random.py

            param:  mean_patience - mean patience in seconds
                    variance_patience - patience variance

            returns - renege time
        '''

        alpha = (mean_patience ** 2) / variance_patience
        beta = variance_patience / mean_patience

        return random.gammavariate(alpha, beta)

    #---------------------------------------------------------------------------

    def assign_chat_duration(self, mean_chat_duration, variance_chat_duration):
        '''
            Getter to assign chat duration
            chat duration follows the gamma distribution
            with alpha and beta values derived from mean and variance
            in the OpenUp MCCIS data

            The python gamma pdf is parametrized with a shape parameter 
            alpha (k) and a scale parameter beta (theta)

            param:  mean_chat_duration - mean chat duration in seconds
                    variance_chat_duration - chat duration variance

            returns - chat duration or MAX_CHAT_DURATION if chat time has exceeded
                service standards
        '''
        alpha = (mean_chat_duration ** 2) / variance_chat_duration
        beta = variance_chat_duration / mean_chat_duration
        duration = random.gammavariate(alpha, beta)
        if duration < MAX_CHAT_DURATION:
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

    def print_idle_counsellors_working(self):
        pprint([x.counsellor_id for x in self.store_counsellors_active.items])

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

    # set up service operation and run simulation until  
    S = ServiceOperation(env=env, ts=fitted_ts, ts_period=ts_period,
        thinning_random=thinning_random, boxcox_lambda=boxcox_lambda,
        use_actual_interarrivals=True)
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
    except ZeroDivisionError:
        percent_served = 0
        percent_served_repeated = 0
        percent_served_regular = 0
    logging.debug(f'4. Total number of Users served: {S.served} ({percent_served:.02f}% of (2) )')
    logging.debug(f'5. Total number of Users served -- repeated user: {S.served_g_repeated} ({percent_served_repeated:.02f}% of (4) )')
    logging.debug(f'6. Total number of Users served -- user: {S.served_g_regular} ({percent_served_regular:.02f}% of (4) )\n')


    logging.debug(f'{Colors.HBLUE}Stage 2b. Number of users reneged given TOS acceptance{Colors.HEND}')
    try:
        percent_reneged = S.reneged/S.num_users_TOS_accepted * 100
    except ZeroDivisionError:
        percent_reneged = 0
    logging.debug(f'7. Total number of Users reneged: {S.reneged} ({percent_reneged:.02f}% of (2) )\n')


    logging.debug(f'{Colors.HBLUE}Queue Status{Colors.HEND}')
    logging.debug(f'8. Maximum user queue length: {S.user_queue_max_length}')
    logging.debug(f'9. Number of instances waiting queue is not empty after first person has been dequeued: {len(S.queue_status)}')
    # logging.debug(f'full last debriefing duration: {S.queue_status}')

if __name__ == '__main__':
    main()