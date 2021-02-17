'''
    This program uses Simpy to simulate the OpenUp Counselling Service 
    platform and user arrivals.  

    Users will renege when they loose patience waiting in the queue

    Interrupt version - When counsellors have to sign out, an interrupt
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
# from scipy.stats import poisson
from simpy.events import AllOf


logging.basicConfig(
    level=logging.ERROR,
    # format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    format='%(message)s',
    filename='debug.log'
)



INTERARRIVALS_FILE = os.path.expanduser(
    '~/csrp/openup-queue-simulation/interarrivals_day_of_week_hour/Sep2020_to_Nov2020/interarrivals_day_of_week_hour.csv')

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
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR     # 1440 minutes per day
SIMULATION_DURATION = MINUTES_PER_DAY * 30  # currently given as num minutes 
                                            #     per day * num days in month

POSTCHAT_FILLOUT_TIME = 20                  # time to fill out counsellor postchat


# also see assign_renege_time() in ServiceOperation class with regards
# to specification of renege parameters 
MEAN_LOG_RENEGE_TIME = 2.72                 # mean of natural log of patience before reneging
SD_LOG_RENEGE_TIME = .856                   # sd of natural log of patience before reneging


# counsellor average chat no longer than 60 minutes
# meaning differences between types of 1/mean_chat_duration will be negligible
MEAN_CHAT_DURATION_COUNSELLOR = {
    'SOCIAL_WORKER': 51.4,                  # Social Worker - average 51.4 minutes
    'DUTY_OFFICER': 56.9,                   # Duty Officer - average 56.9 minutes
    'VOLUNTEER': 57.2,                      # Volunteer - average 57.2 minutes
}


MEAN_CHAT_DURATION_USER = {
    'CRISIS': 105.1,                        # Crisis and High combined to give
    'HIGH': 105.1,                          #     average 105.1 minutes 
    'MEDIUM': 75.7,                         # Medium - average 75.7 minutes
    'LOW': 53.8,                            # Low - average 53.8 minutes
}

SD_CHAT_DURATION_USER = {
    'CRISIS': 72.3,                         # Crisis and High combined to give
    'HIGH': 72.3,                           #     sd 72.3 minutes 
    'MEDIUM': 46.2,                         # Medium - sd 46.2 minutes
    'LOW': 40.7,                            # Low - sd 40.7 minutes
}


MEAL_BREAK_DURATION = 60                    # 60 minute meal break
LAST_CASE_CUTOFF = 30                       # do not assign any more cases 30 minutes before signoff

NUM_DUTY_OFFICERS = {
    'GRAVEYARD': 1,
    'AM': 1,
    'PM': 1,
    'SPECIAL': 0
}

NUM_SOCIAL_WORKERS = {
    'GRAVEYARD': 1,
    'AM': 2,
    'PM': 2,
    'SPECIAL': 3
}

NUM_VOLUNTEERS = {
    'GRAVEYARD': 3,
    'AM': 2,
    'PM': 2,
    'SPECIAL': 4
}

LEN_CIRCULAR_ARRAY = 2000                   # length of circular array
MAX_CHAT_DURATION = 60 * 11                 # longest chat duration is 11 hours (from OpenUp 1.0)

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
    YELLOW = '\033[33m'

    HGREEN = '\x1b[6;37;42m'
    HRED = '\x1b[6;37;41m'
    HWHITE = '\x1b[6;37;47m'
    HBLUE = '\x1b[6;37;44m'
    HEND = '\x1b[0m'

#-------------------------------------------------------------------------------

class DutyOfficerShifts(enum.Enum):
    '''
        different types of paid worker shifts
        shift start, end in minutes
    '''

    GRAVEYARD = ('GRAVEYARD',   True, 1290, 1890)   # from 9:30pm to 7:30am
    AM =        ('AM',          False, 435, 915)    # from 7:15am to 3:15 pm
    PM =        ('PM',          False, 840, 1320)   # from 2pm to 10pm
    SPECIAL =   ('SPECIAL',     True, 1020, 1500)   # from 5pm to 1 am

    def __init__(self, shift_name, is_edge_case, start, end):

        self.shift_name = shift_name
        self.is_edge_case = is_edge_case
        self.start = start
        self.end = end
        self.num_workers = NUM_DUTY_OFFICERS.get(shift_name)

    @property
    def duration(self):
        return int(self.end - self.start)  


    @property
    def meal_start(self):
        '''
            define lunch as the midpoint of shift
            which is written to minimize underflow and overflow problems
        '''
        if self.shift_name == 'GRAVEYARD':
            # this is adjusted with set value of DutyOfficerShift.GRAVEYARD
            return self.start + 240 - 15 # 1:15am
        # else:
        return int(self.start + (self.end - self.start) / 2) % MINUTES_PER_DAY

#-------------------------------------------------------------------------------

class SocialWorkerShifts(enum.Enum):
    '''
        different types of paid worker shifts
        shift start, end in minutes
    '''

    GRAVEYARD = ('GRAVEYARD',   True, 1290, 1890)   # from 9:30pm to 7:30am
    AM =        ('AM',          False, 435, 915)    # from 7:15am to 3:15 pm
    PM =        ('PM',          False, 840, 1320)   # from 2pm to 10pm
    SPECIAL =   ('SPECIAL',     True, 1020, 1500)   # from 5pm to 1 am

    def __init__(self, shift_name, is_edge_case, start, end):

        self.shift_name = shift_name
        self.is_edge_case = is_edge_case
        self.start = start
        self.end = end
        self.num_workers = NUM_SOCIAL_WORKERS.get(shift_name)

    @property
    def duration(self):
        return int(self.end - self.start)  


    @property
    def meal_start(self):
        '''
            define lunch as the midpoint of shift
            which is written to minimize underflow and overflow problems
        '''
        if self.shift_name == 'GRAVEYARD':
            # this is adjusted with set value of DutyOfficerShift.GRAVEYARD
            return self.end - 180 - 15 # so wakes up at 7:15am
        # else:
        return int(self.start + (self.end - self.start) / 2) % MINUTES_PER_DAY
    
#-------------------------------------------------------------------------------

class VolunteerShifts(enum.Enum):
    '''
        different types of unpaid worker shifts
        shift start, end in minutes
    '''

    GRAVEYARD = ('GRAVEYARD',   True, 1200, 1440)   # from 8pm to 12am
    AM =        ('AM',          False, 630, 870)    # from 10:30am to 2:30 pm
    PM =        ('PM',          False, 900, 1140)   # from 3pm to 7pm
    SPECIAL =   ('SPECIAL',     False, 1080, 1320)  # from 6pm to 10pm

    def __init__(self, shift_name, is_edge_case, start, end):

        self.shift_name = shift_name
        self.is_edge_case = is_edge_case
        self.start = start
        self.end = end
        self.num_workers = NUM_VOLUNTEERS.get(shift_name)

    @property
    def duration(self):
        return int(self.end - self.start)

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

    # risk enum | risklevel | non-repeated probability | repeated probability
    CRISIS =    ('CRISIS',  .0,  .0)
    HIGH =      ('HIGH',    .002,  .001)
    MEDIUM =    ('MEDIUM',  .080,  .087)
    LOW =       ('LOW',     .918,  .906)

    def __init__(self, risk, p_non_repeated_user, p_repeated_user):
        self.risk = risk
        self.p_non_repeated_user = p_non_repeated_user
        self.p_repeated_user = p_repeated_user
        self.mean_chat_duration  = MEAN_CHAT_DURATION_USER.get(risk)
        self.variance_chat_duration = SD_CHAT_DURATION_USER.get(risk) ** 2
        
#-------------------------------------------------------------------------------

class Users(enum.Enum):
    '''
        Distribution of Repeated Users - 85% regular / 15% repeated
        among the users accepting TOS
        This ratio is based on repeated user def on counsellor postchat
    '''

    # user enum | user status | user index | probability
    REPEATED =      ('REPEATED_USER',       1,  .15)
    NON_REPEATED =  ('NONREPEATED_USER',    2,  .85)
    
    def __init__(self, user_type, index, probability):
        self.user_type = user_type
        self.index = index # index to access Risklevel probability
        self.probability = probability

#-------------------------------------------------------------------------------

class TOS(enum.Enum):
    '''
        TOS States - here "TOS REJECTED" includes all "TOS NOT ACCEPTED" cases
    '''

    # TOS enum | TOS status
    TOS_ACCEPTED =  'TOS_ACCEPTED'
    TOS_REJECTED =  'TOS_REJECTED'
    
    def __init__(self, status):
        self.status = status

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
        self.client_id = None

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
        postchat_fillout_time=POSTCHAT_FILLOUT_TIME,
        mean_log_renege_time=MEAN_LOG_RENEGE_TIME,
        sd_log_renege_time=SD_LOG_RENEGE_TIME,
        meal_break_duration=MEAL_BREAK_DURATION,
        ):

        '''
            init function

            param:
                env - simpy environment

                postchat_fillout_time - Time alloted to complete the counsellor postchat
                    if not specified, defaults to POSTCHAT_FILLOUT_TIME

                mean_log_renege_time - Mean renege time in minutes.
                    If not specified, defaults to MEAN_LOG_RENEGE_TIME

                sd_log_renege_time  - sd renege time in minutes
                    If not specified, defaults to SD_LOG_RENEGE_TIME

                tea_break_duration - Tea break duration in minutes.
                    If not specified, defaults to NUM_TEA_BREAKS

                meal_break_duration - Meal break duration in minutes.
                    If not specified, defaults to MEAL_BREAK_DURATION
        '''
        self.env = env

        self.__counsellor_postchat_survey_time = postchat_fillout_time
        self.__mean_log_renege_time = mean_log_renege_time
        self.__sd_log_renege_time = sd_log_renege_time
        self.__meal_break = meal_break_duration

        # set interarrivals (a circular array of interarrival times)
        self.__mean_interarrival_time = self.read_interarrivals_csv()
        # logging.debug(self.__mean_interarrival_time)

        # vector of TOS probabilities
        self.__TOS_probabilities = self.read_tos_probabilities_csv()
        # logging.debug(self.__TOS_probabilities)


        # counters and flags (also see properties section)
        self.num_users = 0 # to be changed in create_users()
        self.num_users_TOS_accepted = 0
        self.num_users_TOS_rejected = 0


        self.reneged = 0
        self.reneged_during_transfer = 0
        self.served = 0
        self.served_g_repeated = 0
        self.served_g_regular = 0
 
        self.users_in_system = []
        self.user_queue = []
        self.queue_status = []
        self.queue_time_stats = []
        self.renege_time_stats = []
        self.case_chat_time = []

        self.num_available_counsellor_processes = []

        self.user_queue_max_length = 0


        self.current_shift_start = {
            'GRAVEYARD': None,
            'AM': None,
            'PM': None,
            'SPECIAL': None
        }

        self.current_shift_end = {
            'GRAVEYARD': None,
            'AM': None,
            'PM': None,
            'SPECIAL': None
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


        self.signout_ready_flag = {}
        for s in DutyOfficerShifts:
            self.signout_ready_flag[s] = None


        # create list of counsellors at different shifts
        self.counsellors = {}
        for s in SocialWorkerShifts:
            self.counsellors[s] = []
            self.list_counsellers(s, Roles.SOCIAL_WORKER)
        for s in DutyOfficerShifts:
            self.counsellors[s] = []
            self.list_counsellers(s, Roles.DUTY_OFFICER)
        for s in VolunteerShifts:
            self.counsellors[s] = []
            self.list_counsellers(s, Roles.VOLUNTEER)

        # logging.debug(f'Counsellors Arranged:\n{self.counsellors}')

        # set up idle processes
        self.counsellor_procs_signin[Roles.DUTY_OFFICER] = {s: self.env.process(
            self.counsellors_signin(s) ) for s in DutyOfficerShifts}

        self.counsellor_procs_signout[Roles.DUTY_OFFICER] = {s: self.env.process(
            self.counsellors_signout(s, Roles.DUTY_OFFICER) ) for s in DutyOfficerShifts}



        self.counsellor_procs_signin[Roles.SOCIAL_WORKER] = {s: self.env.process(
            self.counsellors_signin(s) ) for s in SocialWorkerShifts}

        self.counsellor_procs_signout[Roles.SOCIAL_WORKER] = {s: self.env.process(
            self.counsellors_signout(s, Roles.SOCIAL_WORKER) ) for s in SocialWorkerShifts}



        self.counsellor_procs_signin[Roles.VOLUNTEER] = {s: self.env.process(
            self.counsellors_signin(s) ) for s in VolunteerShifts}

        self.counsellor_procs_signout[Roles.VOLUNTEER] = {s: self.env.process(
            self.counsellors_signout(s, Roles.VOLUNTEER) ) for s in VolunteerShifts}


        # set up meal breaks
        self.meal_break_processes[Roles.SOCIAL_WORKER] = {s: self.env.process(
            self.counsellors_break_start(s, Roles.SOCIAL_WORKER) )
            for s in SocialWorkerShifts}

        self.meal_break_processes[Roles.SOCIAL_WORKER] = {s: self.env.process(
            self.counsellors_break_end(s, Roles.SOCIAL_WORKER) )
            for s in SocialWorkerShifts}


        self.meal_break_processes[Roles.DUTY_OFFICER] = {s: self.env.process(
            self.counsellors_break_start(s, Roles.DUTY_OFFICER) )
            for s in DutyOfficerShifts}

        self.meal_break_processes[Roles.DUTY_OFFICER] = {s: self.env.process(
            self.counsellors_break_end(s, Roles.DUTY_OFFICER) )
            for s in DutyOfficerShifts}


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

    @case_chat_time.setter
    def case_chat_time(self, value):
        self.__case_chat_time = value

    ############################################################################
    # counsellor related functions
    ############################################################################

    def list_counsellers(self, shift, role):
        '''
            subroutine to create list of counsellors during a shift

            param:
            shift - one of Shifts/Volunteer Shifts enum
            role - role matching shift, a role enum

            precondition - shift must match with role
        '''            

        # signing in involves creating multiple counsellor processes
        # Use only 1 loop for speed
        for i in range(shift.num_workers*role.num_processes):
            # use integer division and modulo divsion to give indices
            id_ = i // shift.num_workers + 1
            subprocess_num = i % role.num_processes + 1
            
            counsellor_id = f'{shift.shift_name}_{role.counsellor_type}_{id_}_process_{subprocess_num}'
            self.counsellors[shift].append(
                Counsellor(self.env, counsellor_id, shift, role)
            )
            
            # logging.debug(f'list_counsellers shift:{shift.shift_name}\n{self.counsellors[shift]}\n\n')

    #---------------------------------------------------------------------------

    def counsellors_signin(self, shift):
        '''
            routine to sign in counsellors during a shift

            param:
            shift - one of Shifts enum
        '''
        counsellor_init = True
        shift_remaining = None


        # start shift immediately if graveyard or special shift to account for edge case
        # otherwise wait until shift begins
        if not shift.is_edge_case or shift is VolunteerShifts.GRAVEYARD:
            yield self.env.timeout(shift.start) # delay for shift.start minutes
            shift_remaining = shift.duration
        else:
            shift_remaining = shift.end%MINUTES_PER_DAY
        

        while True:
            start_shift_time = self.env.now

            self.current_shift_start[shift.shift_name] = start_shift_time
            self.current_shift_end[shift.shift_name] = start_shift_time + shift_remaining

            for counsellor in self.counsellors[shift]:
                yield self.store_counsellors_active.put(counsellor)

                if start_shift_time > 0:
                    logging.debug(f'{Colors.GREEN}+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}')
                    logging.debug(f'{Colors.GREEN}Counsellor {counsellor.counsellor_id} signed in at t = {start_shift_time}({start_shift_time%MINUTES_PER_DAY:.3f}){Colors.WHITE}')
                    logging.debug(f'{Colors.GREEN}+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}\n')
                
                    # assert start_shift_time % MINUTES_PER_DAY == shift.start or start_shift_time == 0
                    # assert counsellor in self.store_counsellors_active.items

            logging.debug(f'Signed in shift:{shift.shift_name} at {start_shift_time}({int(((start_shift_time)%MINUTES_PER_DAY)//60)%24}).'
                f'  There are {len(self.store_counsellors_active.items)} idle SO counsellor processes:')
            self.log_idle_counsellors_working()

            if shift.is_edge_case and counsellor_init:
                # deal with edge case one more time
                yield self.env.timeout(shift.start)
                shift_remaining = shift.duration
                counsellor_init = False
            else:
                # repeat every 24 hours
                yield self.env.timeout(MINUTES_PER_DAY) 

    #---------------------------------------------------------------------------

    def counsellors_signout(self, shift, role):
        '''
            routine to sign out counsellors during a shift

            param:
            shift - one of Shifts enum
            role - one of Roles enum
        '''

        total_procs = shift.num_workers * role.num_processes

        # delay for shift.end minutes
        # taking the mod to deal with first initialized graveyard or special shifts (edge cases)
        if shift is VolunteerShifts.GRAVEYARD:
            yield self.env.timeout(shift.end)
        else:
            yield self.env.timeout(shift.end % MINUTES_PER_DAY)

        while True:
            counsellors_still_serving = set(self.counsellors[shift]).difference(
                set(self.store_counsellors_active.items) )
            if len(counsellors_still_serving) > 0:
                logging.debug(f'{Colors.BLUE}--------------INCOMPLETE {shift} {self.env.now} ({self.env.now%MINUTES_PER_DAY})--------------{Colors.WHITE}')
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
                lambda x: x.shift is shift and x.role is role)
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
                # assert end_shift_time % MINUTES_PER_DAY == shift.start or end_shift_time == 0
                # assert c not in self.store_counsellors_active.items            

            logging.debug(f'Signed out shift:{shift.shift_name} at {end_shift_time}({int((int(end_shift_time)%MINUTES_PER_DAY)/60)%24}).'
                f'  There are {len(self.store_counsellors_active.items)} idle SO counsellor processes:\n')
            self.log_idle_counsellors_working()

            # repeat every 24 hours - overtime
            yield self.env.timeout(MINUTES_PER_DAY)

    #---------------------------------------------------------------------------

    def counsellors_break_start(self, shift, role):
        '''
            routine to start a meal break

            param:
            shift - one of Shifts enum
            role - one of Roles enum
        '''

        total_procs = shift.num_workers * role.num_processes

        # delay until meal break starts
        yield self.env.timeout(shift.meal_start)

        while True:

            counsellors_still_serving = set(self.counsellors[shift]).difference(
                set(self.store_counsellors_active.items) )
            if len(counsellors_still_serving) > 0:
                logging.debug(f'{Colors.BLUE}--------------INCOMPLETE {shift} {self.env.now} ({self.env.now%MINUTES_PER_DAY})--------------{Colors.WHITE}')
                # logging.debug([c.counsellor_id for c in self.counsellors[shift]])
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
                lambda x: x.shift is shift and x.role is role)
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

            logging.debug(f'Shift {shift.shift_name} taking meal break at {break_init_time}({int((int(break_init_time)%MINUTES_PER_DAY)/60)%24}).'
                f'  There are {len(self.store_counsellors_active.items)} idle SO counsellor processes:\n')
            self.log_idle_counsellors_working()

            # repeat every 24 hours
            yield self.env.timeout(MINUTES_PER_DAY)

    #---------------------------------------------------------------------------

    def counsellors_break_end(self, shift, role):
        '''
            handle to end the meal_break

            param:
            shift - one of Shifts enum
            role - one of Roles enum
        '''

        # delay until meal break starts
        yield self.env.timeout(shift.meal_start + self.__meal_break)

        while True:
            end_break_time = self.env.now

            for counsellor in self.counsellors[shift]:
                yield self.store_counsellors_active.put(counsellor)

                logging.debug(f'{Colors.BLUE}##########################################################################{Colors.WHITE}')
                logging.debug(f'{Colors.BLUE}Counsellor {counsellor.counsellor_id} BAK at t = {end_break_time}({end_break_time%MINUTES_PER_DAY:.3f}){Colors.WHITE}')
                logging.debug(f'{Colors.BLUE}##########################################################################{Colors.WHITE}\n')
                
                # assert start_shift_time % MINUTES_PER_DAY == shift.start or start_shift_time == 0
                # assert counsellor in self.store_counsellors_active.items

            logging.debug(f'Shift {shift.shift_name} resumed at {end_break_time}({int(((end_break_time)%MINUTES_PER_DAY)//60)%24}).'
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
            interarrival_time = self.assign_interarrival_time()
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

            # if TOS accepted, send add user to the queue
            # otherwise increment counter and do nothing
            tos_state = self.assign_TOS_acceptance()
            if tos_state == TOS.TOS_ACCEPTED:
                self.num_users_TOS_accepted += 1
                self.user_handler[i%LEN_CIRCULAR_ARRAY] = self.env.process(
                    self.handle_user(i)
                )

                logging.debug(f'{Colors.GREEN}**************************************************************************{Colors.HEND}')
                logging.debug(f'{Colors.GREEN}User {i} has just accepted TOS.  Chat session created at '
                    f'{self.env.now:.3f}{Colors.HEND}')
                logging.debug(f'{Colors.GREEN}**************************************************************************{Colors.HEND}\n')

            else: # if TOS.TOS_REJECTED
                self.num_users_TOS_rejected += 1

                logging.debug(f'{Colors.BLUE}**************************************************************************{Colors.HEND}')
                logging.debug(f'{Colors.BLUE}User {i} rejected TOS at {self.env.now}{Colors.HEND}')
                logging.debug(f'{Colors.BLUE}**************************************************************************{Colors.HEND}\n')

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

            shift_end = self.current_shift_end.get(x.shift.shift_name)
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


        renege_time = self.assign_renege_time()
        user_status = self.assign_user_status()
        risklevel = self.assign_risklevel(user_status)
        chat_duration = self.assign_chat_duration(
            risklevel.mean_chat_duration, risklevel.variance_chat_duration)
        transfer_case = False # if process is interrupted, this flag is set to True
        self.users_in_system.append(user_id)
        self.user_queue.append(user_id)
        cumulative_chat_time = 0
        chat_finished = False


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


            # store number of available counsellor processes at current timestamp
            self.num_available_counsellor_processes.append(
                (self.env.now, len(self.store_counsellors_active.items) )
            )
                


            if counsellor not in results: # if user reneged
                counsellor.cancel() # cancel counsellor request

                # remove user from system record
                self.users_in_system.remove(user_id)
                if not transfer_case:
                    self.reneged += 1 # update counter
                    log_string = f'{Colors.HBLUE}No counsellor picked up this case{Colors.HEND}'
                else:
                    self.reneged_during_transfer += 1
                    self.case_chat_time.append(cumulative_chat_time)
                    

                    log_string = f'{Colors.HBLUE}The session lasted {cumulative_chat_time:.3f} minutes.\n\n{Colors.HEND}'

                logging.debug(f'{Colors.HRED}**************************************************************************{Colors.HEND}')
                logging.debug(f'{Colors.HRED}User {user_id} reneged after '
                    f'spending t = {renege_time:.3f} minutes in the queue.{Colors.HEND}')
                logging.debug(log_string)
                logging.debug(f'{Colors.HRED}**************************************************************************{Colors.HEND}\n')

                logging.debug(f'Users in system: {self.users_in_system}')

                chat_duration = 0


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


                chat_complete = None
                try:
                    # timeout is chat duration + 20 minutes to fill out postchat survey
                    chat = yield self.env.timeout(chat_duration)
                    chat_complete = True
                    fill_postchat = yield self.env.timeout(
                        self.__counsellor_postchat_survey_time,
                        value=self.env.now
                    ) # if triggered, store timestamp
                    

                except simpy.Interrupt as si:
                    if isinstance(si.cause, tuple) and si.cause[0] in [JobStates.SIGNOUT, JobStates.MEAL_BREAK]:

                        counsellor_to_sign_out = si.cause[-1]
                        if counsellor_instance is counsellor_to_sign_out:

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
                                chat_duration = 0
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

    def assign_interarrival_time(self):
        '''
            Getter to assign interarrival time by the current time interval
            interarrival time follows the exponential distribution

            returns - interarrival time
        '''
        
        # cast this as integer to get a rough estimate
        # calculate the nearest hour as an integer
        # use it to access the mean interarrival time, from which the lambda
        # can be calculated

        current_time = int(self.env.now)
        # logging.debug(f'Current time: {current_time}')

        current_weekday = int(current_time / MINUTES_PER_DAY)
        # logging.debug(f'Current weekday: {current_weekday}')


        current_day_minutes = current_time % MINUTES_PER_DAY
        # logging.debug(f'Current Minutes day: {current_day_minutes}')
        nearest_hour = int(current_day_minutes / 60)
        # logging.debug(f'Nearest hour: {nearest_hour}')
        
        # get the index
        idx = int(24*current_weekday + nearest_hour) % \
            len(self.__mean_interarrival_time)
        # logging.debug(f'index: {idx}')

        lambda_interarrival = 1.0 / self.__mean_interarrival_time[idx]
        return random.expovariate(lambda_interarrival)

    #---------------------------------------------------------------------------

    def assign_renege_time(self):
        '''
            Getter to assign patience to user
            user patience follows a log normal distribution

            The python lognormal pdf implementation is parametrized with 
            sigma (standard deviation) and mu (mean) of the NORMAL distribution,
            This means taking the mean and sd of natural log of the renege data
            is needed.

            For details please see
            https://github.com/python/cpython/blob/master/Lib/random.py

            returns - renege time
        '''
        return random.lognormvariate(
            self.__mean_log_renege_time, self.__sd_log_renege_time)

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
        probability = [x.value[user_type.index] for x in options]

        return random.choices(options, probability)[0]

    #---------------------------------------------------------------------------

    def assign_user_status(self):
        '''
            Getter to assign user status
        '''
        options = list(Users)
        probability = [x.value[-1] for x in options]

        return random.choices(options, probability)[0]

    #---------------------------------------------------------------------------

    def assign_TOS_acceptance(self):
        '''
            Getter to assign TOS status
        '''
        
        current_time = int(self.env.now)
        # logging.debug(f'Current time: {current_time}')

        current_weekday = int(current_time / MINUTES_PER_DAY)
        # logging.debug(f'Current weekday: {current_weekday}')


        current_day_minutes = current_time % MINUTES_PER_DAY
        # logging.debug(f'Current Minutes day: {current_day_minutes}')
        nearest_hour = int(current_day_minutes / 60)
        # logging.debug(f'Nearest hour: {nearest_hour}')
        
        # get the index
        idx = int(24*current_weekday + nearest_hour) % \
            len(self.__TOS_probabilities)
        # logging.debug(f'index: {idx}')

        p_tos_accepted = self.__TOS_probabilities[idx]
        options = list(TOS)

        return random.choices(options, [p_tos_accepted, 1-p_tos_accepted])[0]

    ############################################################################
    # File IO functions
    ############################################################################

    def read_interarrivals_csv(self):
        '''
            file input function to read in mean interarrivals data
            by the day, starting from Sunday 0h, ending at Saturday 23h      
        '''
        try:
            with open(INTERARRIVALS_FILE, 'r') as f:
                weekday_hours = [float(i.split(',')[-2][:-1])
                    for i in f.readlines()[1:] ]

            return weekday_hours

        except Exception as e:
            print('Unable to read interarrivals file.')

    #---------------------------------------------------------------------------

    def read_tos_probabilities_csv(self):
        '''
            file input function to read in TOS acceptance probability data
            by the day, starting from Sunday 0h, ending at Saturday 23h      
        '''
        try:
            with open(INTERARRIVALS_FILE, 'r') as f:
                probabilities = [float(i.split(',')[-1][:-1])
                    for i in f.readlines()[1:] ]

            return probabilities

        except Exception as e:
            print('Unable to read TOS probabilities file.')

    #---------------------------------------------------------------------------

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

    random.seed(SEED)

    # # create environment
    env = simpy.Environment() 

    # set up service operation and run simulation until  
    S = ServiceOperation(env=env)
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
    logging.debug(f'7. Total number of Users reneged when assigned to the (first) counsellor: {S.reneged} ({percent_reneged:.02f}% of (2) )\n')
    logging.debug(f'8. Total number of Users reneged during a case transfer: {S.reneged_during_transfer}')


    logging.debug(f'{Colors.HBLUE}Queue Status{Colors.HEND}')
    logging.debug(f'9. Maximum user queue length: {S.user_queue_max_length}')
    logging.debug(f'10. Number of instances waiting queue is not empty after first person has been dequeued: {len(S.queue_status)}')
    # logging.debug(f'full last debriefing duration: {S.queue_status}')

if __name__ == '__main__':
    main()