'''
    This program uses Simpy to simulate the OpenUp Counselling Service 
    platform and helpseeker arrivals.  

    Helpseekers will renege when they loose patience waiting in the queue

    For more details about the Simpy syntax, please visit
    https://simpy.readthedocs.io/en/latest/contents.html

    Discrete Event Simulation Primer:
    https://www.academia.edu/35846791/Discrete_Event_Simulation._It_s_Easy_with_SimPy_

    International Worker Meal and Teabreak Standards:
    https://www.ilo.org/wcmsp5/groups/public/---ed_protect/---protrav/---travail/documents/publication/wcms_491374.pdf
'''

import simpy, random, enum, itertools, os
from simpy.util import start_delayed
from pprint import pprint
# from scipy.stats import poisson
from simpy.events import AllOf


INTERARRIVALS_FILE = os.path.expanduser(
    '~/csrp/openup-analysis/interarrivals_day_of_week_hour/Dec2019_to_Feb2020/interarrivals_day_of_week_hour.csv')

################################################################################ 
# Globals
################################################################################

QUEUE_THRESHOLD = 0                         # memoize data if queue is >= threshold 
DAYS_IN_WEEK = 7                            # 7 days in a week
MINUTES_PER_HOUR = 60                       # 60 minutes in an hour

MAX_SIMULTANEOUS_CHATS_SOCIAL_WORKER = 3    # Social Worker can process max 2 chats
MAX_SIMULTANEOUS_CHATS_DUTY_OFFICER = 1     # Duty Officer can process max 1 chat
MAX_SIMULTANEOUS_CHATS_VOLUNTEER = 2        # Volunteer can process max 1 chat

SEED = 728                                  # for seeding the sudo-random generator
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR     # 1440 minutes per day
SIMULATION_DURATION = MINUTES_PER_DAY * 30  # currently given as num minutes 
                                            #     per day * num days in month

POSTCHAT_FILLOUT_TIME = 20                  # time to fill out counsellor postchat
MEAN_RENEGE_TIME = 2.3                      # mean patience before reneging


# average chat no longer than 60 minutes
MEAN_CHAT_DURATION_SOCIAL_WORKER = 52.4
MEAN_CHAT_DURATION_DUTY_OFFICER = 58.5
MEAN_CHAT_DURATION_VOLUNTEER = 55.7
MEAN_CHAT_DURATION = 45#55


TEA_BREAK_DURATION = 15                     # 20 minute tea break
MEAL_BREAK_DURATION = 60                    # 60 minute meal break
DEBRIEF_DURATION = 60                       # 60 minute debriefing session per day
TRAINING_DURATION = 480                     # 8 hour training session - once per month
LAST_CASE_CUTOFF = 30 

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

    GRAVEYARD = ('GRAVEYARD',   True, 1290, 1890, 840, 1, 1290, 30, 435, 15) # from 9:30pm to 7:30am
    AM =        ('AM',          False, 435, 915, 960, 1, 435, 15, 840, 15)   # from 7:15am to 3:15 pm
    PM =        ('PM',          False, 840, 1320, 960, 1, 840, 15, 1290, 30)  # from 2pm to 10pm
    SPECIAL =   ('SPECIAL',     True, 1020, 1500, 960, 0, None, None, None, None) # from 5pm to 1 am

    def __init__(self, shift_name, is_edge_case,
        start, end, offset,
        num_workers,
        first_debriefing, first_debriefing_duration,
        last_debriefing, last_debriefing_duration):

        self.shift_name = shift_name
        self.is_edge_case = is_edge_case
        self.start = start
        self.end = end
        self.offset = offset
        self.num_workers = num_workers
        self.first_debriefing = first_debriefing
        self.first_debriefing_duration = first_debriefing_duration
        self.last_debriefing = last_debriefing
        self.last_debriefing_duration = last_debriefing_duration

    @property
    def duration(self):
        return int(self.end - self.start)  


    @property
    def meal_start(self):
        '''
            define lunch as the midpoint of shift
            which is written to minimize underflow and overflow problems
        '''
        return int(self.start + (self.end - self.start) / 2)


    @property
    def first_tea_start(self):
        '''
            first tea break two hours after the shift has started
        '''
        return int(self.start + 120)


    @property
    def last_tea_start(self):
        '''
            tea break two hours before the shift ends
        '''
        return int(self.end - 120)


    @property
    def total_debriefing_duration(self):
        '''
            total debriefing duration
        '''
        return first_debriefing_duration + last_debriefing_duration

#-------------------------------------------------------------------------------

class SocialWorkerShifts(enum.Enum):
    '''
        different types of paid worker shifts
        shift start, end, and next shift offset in minutes
    '''

    GRAVEYARD = ('GRAVEYARD',   True, 1290, 1890, 840, 4)   # from 9:30pm to 7:30am
    AM =        ('AM',          False, 435, 915, 960, 2)    # from 7:15am to 3:15 pm
    PM =        ('PM',          False, 840, 1320, 960, 2)   # from 2pm to 10pm
    SPECIAL =   ('SPECIAL',     True, 1020, 1500, 960, 4)   # from 5pm to 1 am

    def __init__(self, shift_name, is_edge_case, 
        start, end, offset,
        num_workers):

        self.shift_name = shift_name
        self.is_edge_case = is_edge_case
        self.start = start
        self.end = end
        self.offset = offset
        self.num_workers = num_workers

    @property
    def duration(self):
        return int(self.end - self.start)  


    @property
    def meal_start(self):
        '''
            define lunch as the midpoint of shift
            which is written to minimize underflow and overflow problems
        '''
        return int(self.start + (self.end - self.start) / 2)


    @property
    def first_tea_start(self):
        '''
            first tea break two hours after the shift has started
        '''
        return int(self.start + 120)


    @property
    def last_tea_start(self):
        '''
            tea break two hours before the shift ends
        '''
        return int(self.end - 120)
    
#-------------------------------------------------------------------------------

class VolunteerShifts(enum.Enum):
    '''
        different types of unpaid worker shifts
        shift start, end, and next shift offset in minutes
    '''

    GRAVEYARD = ('GRAVEYARD',   True, 1200, 1440, 1200, 3)  # from 8pm to 12am
    AM =        ('AM',          False, 630, 870, 1200, 2)   # from 10:30am to 2:30 pm
    PM =        ('PM',          False, 900, 1140, 1200, 2)  # from 3pm to 7pm
    SPECIAL =   ('SPECIAL',     False, 1080, 1320, 1200, 4)  # from 6pm to 10pm

    def __init__(self, shift_name, is_edge_case, start, end, offset,
        num_workers):

        self.shift_name = shift_name
        self.is_edge_case = is_edge_case
        self.start = start
        self.end = end
        self.offset = offset
        self.num_workers = num_workers

    @property
    def duration(self):
        return int(self.end - self.start)

    @property
    def first_tea_start(self):
        '''
            tea break two hours after the shift has started
        '''
        return int(self.start + 120)

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
    CHAT =          ('CHAT',            20)
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

    # risk enum | risklevel | non-repeated probability | repeated probability
    CRISIS =    ('CRISIS',  .001,  .004)
    HIGH =      ('HIGH',    .008,  .014)
    MEDIUM =    ('MEDIUM',  .154,  .164)
    LOW =       ('LOW',     .837,  .818)

    def __init__(self, risk, p_non_repeated_user, p_repeated_user):
        self.risk = risk
        self.p_non_repeated_user = p_non_repeated_user
        self.p_repeated_user = p_repeated_user
        
#-------------------------------------------------------------------------------

class Users(enum.Enum):
    '''
        Distribution of Repeated Users - 69% regular / 31% repeated
        This ratio is based on repeated user def on counsellor postchat
    '''

    # user enum | user status | user index | probability
    REPEATED =      ('REPEATED_USER',       1,  .31)
    NON_REPEATED =  ('NONREPEATED_USER',    2,  .69)
    
    def __init__(self, user_type, index, probability):
        self.user_type = user_type
        self.index = index # index to access Risklevel probability
        self.probability = probability

#-------------------------------------------------------------------------------

class TOS(enum.Enum):
    '''
        Distribution of Repeated Users - 69% regular / 31% repeated
        This ratio is based on repeated user def on counsellor postchat
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

    SOCIAL_WORKER = ('SOCIAL_WORKER',   MAX_SIMULTANEOUS_CHATS_SOCIAL_WORKER ,
        52.4,   True,   True, False)
    DUTY_OFFICER =  ('DUTY_OFFICER',    MAX_SIMULTANEOUS_CHATS_DUTY_OFFICER ,
        58.5,   True,   True, False)
    VOLUNTEER =     ('VOLUNTEER',       MAX_SIMULTANEOUS_CHATS_VOLUNTEER ,
        55.7,   False,  True, False)

    def __init__(self, counsellor_type, num_processes, mean_chat_duration, 
        meal_break, first_tea_break, last_tea_break):
        self.counsellor_type = counsellor_type
        self.num_processes = num_processes
        self.mean_chat_duration = mean_chat_duration
        self.meal_break = meal_break
        self.first_tea_break = first_tea_break
        self.last_tea_break = last_tea_break

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

    def __init__(self, env, counsellor_id, shift, role):
        '''
            param:

            env - simpy environment instance
            counsellor_id - an assigned counsellor id (STRING)
            shift - counsellor shift (one of Shifts enum)
        '''

        self.env = env
        self.counsellor_id = counsellor_id

        self.taken_first_tea_break = False
        self.taken_last_tea_break = False
        self.taken_lunch_break = False
        
        self.shift = shift
        self.role = role
        self.priority = None # to be set later

    ############################################################################
    # Properties (for encapsulation)
    ############################################################################

    @property
    def taken_first_tea_break(self):
        return self.__taken_first_tea_break

    @property
    def taken_last_tea_break(self):
        return self.__taken_last_tea_break

    @property
    def taken_lunch_break(self):
        return self.__taken_lunch_break



    @taken_first_tea_break.setter
    def taken_first_tea_break(self, value):
        if isinstance(value, bool):
            self.__taken_first_tea_break = value

    @taken_last_tea_break.setter
    def taken_last_tea_break(self, value):
        if isinstance(value, bool):
            self.__taken_last_tea_break = value

    @taken_lunch_break.setter
    def taken_lunch_break(self, value):
        if isinstance(value, bool):
            self.__taken_lunch_break = value

    def reset(self):
        '''
            Function to reset all break flags
        '''
        taken_first_tea_break(False)
        taken_last_tea_break(False)
        taken_lunch_break(False)

#--------------------------------------------------------end of Counsellor class

class ServiceOperation:
    '''
        Class to emulate OpenUp Service Operation with a limited number of 
        counsellors to handle helpseeker chat requests during different shifts

        Helpseekers have to request a counsellor to begin the counselling
        process
    '''

    # total_recruits = 0 # total number counsellors recruited
    # for shift in list(Shifts):
    #     total_recruits += shift.capacity
        
    #---------------------------------------------------------------------------

    def __init__(self, *, env, 
        postchat_fillout_time=POSTCHAT_FILLOUT_TIME,
        mean_renege_time=MEAN_RENEGE_TIME,
        tea_break_duration=TEA_BREAK_DURATION,
        meal_break_duration=MEAL_BREAK_DURATION,
        training_duration=TRAINING_DURATION):

        '''
            init function

            param:
                env - simpy environment

                postchat_fillout_time - Time alloted to complete the counsellor postchat
                    if not specified, defaults to POSTCHAT_FILLOUT_TIME

                mean_renege_time - Mean renege time in minutes.
                    If not specified, defaults to MEAN_RENEGE_TIME

                mean_chat_duration - Mean chat duration in minutes.
                    If not specified, defaults to MEAN_CHAT_DURATION

                tea_break_duration - Tea break duration in minutes.
                    If not specified, defaults to NUM_TEA_BREAKS

                meal_break_duration - Meal break duration in minutes.
                    If not specified, defaults to MEAL_BREAK_DURATION

                training_duration - duration of training session
                    If not specified, defaults to TRAINING_DURATION
        '''
        self.env = env

        self.__counsellor_postchat_survey = postchat_fillout_time
        self.__mean_renege_time = mean_renege_time
        self.__tea_break = tea_break_duration
        self.__meal_break = meal_break_duration
        self.__training_duration = training_duration

        # set interarrivals (a circular array of interarrival times)
        self.__mean_interarrival_time = self.read_interarrivals_csv()
        # print(self.__mean_interarrival_time)

        # vector of TOS probabilities
        self.__TOS_probabilities = self.read_tos_probabilities_csv()
        # print(self.__TOS_probabilities)


        # counters and flags (also see properties section)
        self.num_helpseekers = 0 # to be changed in create_helpseekers()
        self.num_helpseekers_TOS_accepted = 0
        self.num_helpseekers_TOS_rejected = 0


        self.reneged = 0
        self.served = 0
        self.served_g_repeated = 0
        self.served_g_regular = 0
 
        self.helpseeker_in_system = []
        self.helpseeker_queue = []
        self.queue_status = []
        self.queue_time_stats = []
        self.renege_time_stats = []

        self.num_available_counsellor_processes = []

        self.helpseeker_queue_max_length = 0


        self.processes = {} # the main idle process

        # other processes to interrupt the main process
        self.meal_break_processes = {} 
        self.first_tea_break_processes = {}
        self.last_tea_break_processes = {}
        

        # service operation is given an infinite counsellor intake capacity
        # to accomodate four counsellor shifts (see enum Shifts for details)
        self.store_counsellors_active = simpy.FilterStore(env)

        # create counsellors
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

        # print('Counsellors Arranged:')
        # pprint(self.counsellors)

        # set up idle processes
        self.processes[Roles.DUTY_OFFICER] = {s: self.env.process(
            self.counsellors_idle(s, Roles.DUTY_OFFICER) ) for s in DutyOfficerShifts}
        self.processes[Roles.SOCIAL_WORKER] = {s: self.env.process(
            self.counsellors_idle(s, Roles.SOCIAL_WORKER) ) for s in SocialWorkerShifts}
        self.processes[Roles.VOLUNTEER] = {s: self.env.process(
            self.counsellors_idle(s, Roles.VOLUNTEER) ) for s in VolunteerShifts}

        # set up meal breaks
        # self.meal_break_processes[Roles.SOCIAL_WORKER] = {s: self.env.process(
        #     self.counsellors_break(s, Roles.SOCIAL_WORKER, JobStates.MEAL_BREAK) )
        #     for s in SocialWorkerShifts}

        # self.meal_break_processes[Roles.DUTY_OFFICER] = {s: self.env.process(
        #     self.counsellors_break(s, Roles.DUTY_OFFICER, JobStates.MEAL_BREAK) )
        #     for s in DutyOfficerShifts}


        # set up tea breaks and meal breaks
        # paid counsellors get two tea breaks
        # volunteers get one tea break
        # self.first_tea_break_processes[Roles.SOCIAL_WORKER] = {s:self.env.process(
        #     self.counsellors_break(s, Roles.SOCIAL_WORKER, JobStates.FIRST_TEA) )
        #     for s in SocialWorkerShifts}
        # self.first_tea_break_processes[Roles.DUTY_OFFICER] = {s:self.env.process(
        #     self.counsellors_break(s, Roles.DUTY_OFFICER, JobStates.FIRST_TEA) )
        #     for s in DutyOfficerShifts}
        # self.first_tea_break_processes[Roles.VOLUNTEER] = {s:self.env.process(
        #     self.counsellors_break(s, Roles.VOLUNTEER, JobStates.FIRST_TEA) )
        #     for s in VolunteerShifts}
        # self.last_tea_break_processes[Roles.SOCIAL_WORKER] = {s:self.env.process(
        #     self.counsellors_break(s, Roles.SOCIAL_WORKER, JobStates.LAST_TEA) )
        #     for s in SocialWorkerShifts}
        # self.last_tea_break_processes[Roles.DUTY_OFFICER] = {s:self.env.process(
        #     self.counsellors_break(s, Roles.DUTY_OFFICER, JobStates.LAST_TEA) )
        #     for s in DutyOfficerShifts}



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
            
        print(f'create_counsellors shift:{shift.shift_name}\n{self.counsellors[shift]}\n\n')

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
                try:
                    start_shift_time = self.env.now

                    if shift.is_edge_case and counsellor_init_2:
                        scheduled_end_shift_time = start_shift_time + shift_remaining
                        counsellor_init_2 = False

                    else:
                        scheduled_end_shift_time = start_shift_time + shift.duration


                    if shift_remaining == shift.duration or shift_remaining == shift.end%MINUTES_PER_DAY:
                        # begin shift by putting counsellors in the store
                        for counsellor in self.counsellors[shift]:
                            print(f'\n{Colors.GREEN}++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}')
                            print(f'{Colors.GREEN}Counsellor {counsellor.counsellor_id} signed in at t = {start_shift_time:.3f}{Colors.WHITE}')
                            print(f'{Colors.GREEN}++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}\n')
                            # assert start_shift_time % MINUTES_PER_DAY == shift.start or start_shift_time == 0
                            assert counsellor not in self.store_counsellors_active.items
                            yield self.store_counsellors_active.put(counsellor)

                        print(f'Signed in shift:{shift.shift_name} at {start_shift_time}.'
                            f'  There are {len(self.store_counsellors_active.items)} idle SO counsellor processes:')
                        self.print_idle_counsellors_working()
                        print()

                    yield self.env.timeout(shift_remaining) # delay for shift.start minutes
                    


                    # allow only counsellors at a role and a shift to take break
                    counsellor_procs = [self.store_counsellors_active.get(
                        lambda x: x.shift is shift and x.role is role)
                        for _ in range(total_procs)]

                    # wait for all procs
                    counsellor = yield AllOf(self.env, counsellor_procs)

                    # get all nested counsellor instances
                    counsellor_instances = [counsellor[list(counsellor)[i]] for i in range(total_procs)]

                    actual_end_shift_time = self.env.now
                    for c in counsellor_instances:
                        print(f'\n{Colors.RED}--------------------------------------------------------------------------{Colors.WHITE}')
                        print(f'{Colors.RED}Counsellor {c.counsellor_id} signed out at t = {actual_end_shift_time:.3f}.  Overtime: {(actual_end_shift_time-scheduled_end_shift_time):.3f} minutes{Colors.WHITE}')
                        print(f'{Colors.RED}--------------------------------------------------------------------------{Colors.WHITE}\n')
                        # assert time_now % MINUTES_PER_DAY == shift.start or time_now == 0
                        assert counsellor not in self.store_counsellors_active.items

                    print(f'Signed out shift:{shift.shift_name} at {self.env.now}.'
                        f'  There are {len(self.store_counsellors_active.items)} idle SO counsellor processes:')
                    self.print_idle_counsellors_working()
                    print()

                    shift_remaining = 0 # exit loop


                # interrupting the idle process
                # if meal or tea break process throws interrupt
                except simpy.Interrupt as si:
                    counsellor_procs = [self.store_counsellors_active.get(
                        lambda x: x.shift is shift and x.role is role)
                        for _ in range(total_procs)]

                    # wait for all procs
                    counsellor = yield AllOf(self.env, counsellor_procs)
                        
                    # get all nested counsellor instances
                    counsellor_instances = [counsellor[list(counsellor)[i]]
                        for i in range(total_procs)]

                    if si.cause is JobStates.MEAL_BREAK:
                        break_duration = self.__meal_break
                    else:
                        break_duration = self.__tea_break

                    for c in counsellor_instances:
                        print(f'\n{Colors.BLUE}**************************************************************************{Colors.WHITE}')
                        print(f'{Colors.BLUE}Counsellor {c.counsellor_id} AFK for {si.cause} at t = {self.env.now}{Colors.WHITE}')
                        print(f'{Colors.BLUE}**************************************************************************{Colors.WHITE}\n')

                    yield self.env.timeout(break_duration) # take a break
                    print(f'AFK shift:{shift.shift_name}, {role} at {self.env.now}.'
                        f'  There are {len(self.store_counsellors_active.items)} idle SO counsellor processes:')
                    self.print_idle_counsellors_working()
                    print()


                    # send counsellor back to work after break
                    for c in counsellor_instances:
                        print(f'\n{Colors.BLUE}##########################################################################{Colors.WHITE}')
                        print(f'{Colors.BLUE}Counsellor {c.counsellor_id} BAK from {si.cause} at t = {self.env.now}{Colors.WHITE}')
                        print(f'{Colors.BLUE}##########################################################################{Colors.WHITE}\n')
                        yield self.store_counsellors_active.put(c)
                            
                    print(f'BAK shift:{shift.shift_name} at {self.env.now}.'
                        f'  There are {len(self.store_counsellors_active.items)} idle SO counsellor processes:')
                    self.print_idle_counsellors_working()
                    print()

                    shift_remaining -= self.env.now - start_shift_time # update remaining shift


            overtime = actual_end_shift_time - scheduled_end_shift_time
            next_offset = shift.offset - overtime
            # print(f'Overtime: {overtime}, actual end shift {actual_end_shift_time}, scheduled end shift {scheduled_end_shift_time} ')
            # print(f'Next shift offset: {next_offset}, actual: {shift.offset}')
            
            # wait offset minutes - overtime for next shift
            # this fixes the edge case when counsellor goes overtime and
            # the next shift begins from overtime + offset
            yield self.env.timeout(next_offset)

    #---------------------------------------------------------------------------

    def counsellors_break(self, shift, role, break_type):
        '''
            handle to give counsellors a break after a certain period

            param:
            shift - one of Shifts enum
            role - one of Roles enum
        '''
        if break_type is JobStates.MEAL_BREAK:
            yield self.env.timeout(shift.meal_start%MINUTES_PER_DAY) # wait until start of shift to begin shift
        elif break_type is JobStates.FIRST_TEA:
            yield self.env.timeout(shift.first_tea_start%MINUTES_PER_DAY)
        elif break_type is JobStates.LAST_TEA and role is Roles.VOLUNTEER:
            yield self.env.timeout(shift.last_tea_start%MINUTES_PER_DAY)
        else:
            return # do nothing

        while True:
            self.processes[role][shift].interrupt(break_type)
            yield self.env.timeout(MINUTES_PER_DAY) # repeat at this interval

    ############################################################################
    # helpseeker related functions
    ############################################################################

    def create_helpseekers(self):
        '''
            function to generate helpseekers in the background
            at "interarrival_time" invervals to mimic helpseeker interarrivals
        '''

        for i in itertools.count(1): # use this instead of while loop 
                                     # for efficient looping

            # space out incoming helpseekers
            interarrival_time = self.assign_interarrival_time()
            yield self.env.timeout(interarrival_time)

            self.num_helpseekers += 1 # increment counter

            # if TOS accepted, send add helpseeker to the queue
            # otherwise increment counter and do nothing
            tos_state = self.assign_TOS_acceptance()
            if tos_state == TOS.TOS_ACCEPTED:
                self.num_helpseekers_TOS_accepted += 1
                self.env.process(self.handle_helpseeker(i) )
            else: # if TOS.TOS_REJECTED
                self.num_helpseekers_TOS_rejected += 1

    #---------------------------------------------------------------------------

    def handle_helpseeker(self, helpseeker_id):

        '''
            helpseeker process handler

            this function deals with "wait", "renege", and "chat" states
            in the helpseeker state diagram

            param:
                helpseeker_id - helpseeker id
        '''

        # lambda filters
        def case_cutoff(x):
            return x.shift.end%MINUTES_PER_DAY - int(self.env.now)%MINUTES_PER_DAY > LAST_CASE_CUTOFF

        def get_counsellor(x, risk):
            if risk in [Risklevels.HIGH, Risklevels.CRISIS]:
                return x.role is Roles.DUTY_OFFICER
            else:
                return x.role in [Roles.SOCIAL_WORKER, Roles.VOLUNTEER]


        renege_time = self.assign_renege_time()
        helpseeker_status = self.assign_user_status()
        risklevel = self.assign_risklevel(helpseeker_status)
        chat_duration = self.assign_chat_duration()#counsellor_instance.role)
        process_helpseeker = chat_duration + self.__counsellor_postchat_survey

        init_flag = True
        while process_helpseeker:
            start_time = self.env.now

            if init_flag:
                print(f'\n{Colors.HGREEN}**************************************************************************{Colors.HEND}')
                print(f'{Colors.HGREEN}Helpseeker -- {helpseeker_id} has just accepted TOS.  Chat session created at '
                        f'{start_time:.3f}{Colors.HEND}')
                print(f'{Colors.HGREEN}**************************************************************************{Colors.HEND}\n')

                self.helpseeker_in_system.append(helpseeker_id)
                self.helpseeker_queue.append(helpseeker_id)


            # wait for a counsellor matching role or renege
            # get only counsellors matching risklevel to role
            # and remaining shift > LAST_CASE_CUTOFF
            counsellor = self.store_counsellors_active.get(
                lambda x: case_cutoff(x) and get_counsellor(x, risklevel))

            results = yield counsellor | self.env.timeout(renege_time)

            # record the time spent in the queue
            current_time = self.env.now
            time_spent_in_queue = current_time - start_time
            if counsellor in results:    
                current_day_minutes = int(current_time) % MINUTES_PER_DAY
                self.queue_time_stats.append(
                    (f'weekday:{int(current_time / MINUTES_PER_DAY) % DAYS_IN_WEEK}',
                    f'hour:{int(current_day_minutes / MINUTES_PER_HOUR)}',
                    f'time_spent_in_queue:{time_spent_in_queue}')
                )
            else:
                current_day_minutes = int(current_time) % MINUTES_PER_DAY
                self.renege_time_stats.append(
                    (f'weekday:{int(current_time / MINUTES_PER_DAY) % DAYS_IN_WEEK}',
                    f'hour:{int(current_day_minutes / MINUTES_PER_HOUR)}',
                    f'time_spent_in_queue:{renege_time}')
                )


            # dequeue helpseeker in the waiting queue
            self.helpseeker_queue.remove(helpseeker_id) 
            current_helpseeker_queue_length = len(self.helpseeker_queue)

            # update maximum helpseeker queue length
            if current_helpseeker_queue_length > self.helpseeker_queue_max_length:
                self.helpseeker_queue_max_length = current_helpseeker_queue_length

                # print(f'Updated max queue length to '
                #     f'{self.helpseeker_queue_max_length}.\n'
                #     f'Helpseeker Queue: {self.helpseeker_queue}\n\n\n')


            # update queue status
            if current_helpseeker_queue_length >= QUEUE_THRESHOLD:
                current_time = self.env.now
                current_day_minutes = int(current_time) % MINUTES_PER_DAY
                # print(f'weekday: {int(current_time / MINUTES_PER_DAY)} - hour: '
                #     f'{int(current_day_minutes / 60)}, Queue Length: '
                #     f'{current_helpseeker_queue_length}')

                self.queue_status.append(
                    (f'weekday:{int(current_time / MINUTES_PER_DAY) % DAYS_IN_WEEK}',
                    f'hour:{int(current_day_minutes / MINUTES_PER_HOUR)}',
                    f'queue_length:{current_helpseeker_queue_length}'
                ))  

            # print(f'Current Helpseeker Queue contains: {self.helpseeker_queue}')


            # store number of available counsellor processes at time
            self.num_available_counsellor_processes.append(
                (self.env.now, len(self.store_counsellors_active.items) )
            )
            


            if counsellor not in results: # if helpseeker reneged
                # remove helpseeker from system record
                self.helpseeker_in_system.remove(helpseeker_id)
                # print(f'Helpseeker in system: {self.helpseeker_in_system}')
                time_spent_in_queue = renege_time

                print(f'\n{Colors.HRED}**************************************************************************{Colors.HEND}')
                print(f'{Colors.HRED}Helpseeker {helpseeker_id} reneged after '
                    f'spending t = {renege_time:.3f} minutes in the queue.{Colors.HEND}')
                print(f'{Colors.HRED}**************************************************************************{Colors.HEND}\n')
                self.reneged += 1 # update counter
                counsellor.cancel() # cancel counsellor request
                process_helpseeker = 0
                init_flag = False



            else: # if counsellor takes in a helpseeker
                start_time = self.env.now
                counsellor_instance = results[list(results)[0]] #unpack the counsellor instance

                try:
                    print(f'\n{Colors.HGREEN}**************************************************************************{Colors.HEND}')
                    print(f'{Colors.HGREEN}Helpseeker {helpseeker_id} is assigned to '
                        f'{counsellor_instance.counsellor_id} at {self.env.now:.3f}{Colors.HEND}')
                    print(f'{Colors.HGREEN}**************************************************************************{Colors.HEND}\n')

                    # timeout is chat duration + 20 minutes to fill out postchat survey
                    yield self.env.timeout(process_helpseeker)

                    # put the counsellor back into the store, so it will be available
                    # to the next helpseeker
                    print(f'\n{Colors.HBLUE}**************************************************************************{Colors.HEND}')
                    print(f'{Colors.HBLUE}Helpseeker {helpseeker_id}\'s counselling session lasted t = '
                        f'{chat_duration:.3f} minutes.\nCounsellor {counsellor_instance.counsellor_id} '
                        f'is now available at {self.env.now:.3f}.{Colors.HEND}')
                    print(f'{Colors.HBLUE}**************************************************************************{Colors.HEND}\n')


                    # remove helpseeker from system record
                    self.helpseeker_in_system.remove(helpseeker_id)
                    # print(f'Helpseeker in system: {self.helpseeker_in_system}')

                    yield self.store_counsellors_active.put(counsellor_instance)

                    self.served += 1 # update counter
                    if helpseeker_status is Users.REPEATED:
                        self.served_g_repeated += 1
                    else:
                        self.served_g_regular += 1

                    process_helpseeker = 0

                except simpy.Interrupt as si:
                    process_helpseeker -= self.env.now - start
                    init_flag = False

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

    def assign_chat_duration(self):#, role):
        '''
            Getter to assign chat duration
            chat duration follows the gamma distribution (exponential if a=1)

            param: role - one of role enum
        '''
        # lambda_chat_duration = 1.0 / role.mean_chat_duration
        lambda_chat_duration = 1.0 / MEAN_CHAT_DURATION
        return random.expovariate(lambda_chat_duration)
        # return random.gammavariate(2, lambda_chat_duration)

    #---------------------------------------------------------------------------

    def assign_risklevel(self, helpseeker_type):
        '''
            Getter to assign risklevels

            param: helpseeker_type - one of either Users enum
        '''
        options = list(Risklevels)
        probability = [x.value[helpseeker_type.index] for x in options]

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
        # print(f'Current time: {current_time}')

        current_weekday = int(current_time / MINUTES_PER_DAY)
        # print(f'Current weekday: {current_weekday}')


        current_day_minutes = current_time % MINUTES_PER_DAY
        # print(f'Current Minutes day: {current_day_minutes}')
        nearest_hour = int(current_day_minutes / 60)
        # print(f'Nearest hour: {nearest_hour}')
        
        # get the index
        idx = int(24*current_weekday + nearest_hour) % \
            len(self.__TOS_probabilities)
        # print(f'index: {idx}')

        p_tos_accepted = self.__TOS_probabilities[idx]
        options = list(TOS)

        return random.choices(options, [p_tos_accepted, 1-p_tos_accepted])[0]

    ############################################################################
    # File IO functions
    ############################################################################

    def read_interarrivals_csv(self):
        '''
            file input function to read in interarrivals data
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

    def print_idle_counsellors_working(self):
        pprint([x.counsellor_id for x in self.store_counsellors_active.items])

#--------------------------------------------------end of ServiceOperation class

################################################################################
# Main Function
################################################################################

def main():
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    print('Initializing OpenUp Queue Simulation')
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')

    # random.seed(SEED) # comment out line if not reproducing results
    # random.seed(744)

    # # create environment
    env = simpy.Environment() 

    # set up service operation and run simulation until  
    S = ServiceOperation(env=env)
    env.run(until=SIMULATION_DURATION)
    # # print(S.assign_risklevel() )

    print('\n\n\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    print(f'Final Results ')#-- number of simultaneous chats: {MAX_NUM_SIMULTANEOUS_CHATS}')
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')


    print(f'{Colors.HBLUE}Stage 1. TOS Acceptance{Colors.HEND}')
    try:
        percent_accepted_TOS = S.num_helpseekers_TOS_accepted/S.num_helpseekers * 100
        percent_rejected_TOS = 100 - percent_accepted_TOS
    except ZeroDivisionError:
        percent_accepted_TOS = 0
        percent_rejected_TOS = 0
    print(f'1. Total number of Helpseekers visited OpenUp: {S.num_helpseekers}')
    print(f'2. Total number of Helpseekers accepted TOS: {S.num_helpseekers_TOS_accepted} ({percent_accepted_TOS:.02f}% of (1) )')
    print(f'3. Total number of Helpseekers rejected TOS: {S.num_helpseekers_TOS_rejected} ({percent_rejected_TOS:.02f}% of (1) )\n')


    print(f'{Colors.HBLUE}Stage 2a. Number of users served given TOS acceptance{Colors.HEND}')
    try:
        percent_served = S.served/S.num_helpseekers_TOS_accepted * 100
        percent_served_repeated = S.served_g_repeated/S.served * 100
        percent_served_regular = S.served_g_regular/S.served * 100
    except ZeroDivisionError:
        percent_served = 0
        percent_served_repeated = 0
        percent_served_regular = 0
    print(f'4. Total number of Helpseekers served: {S.served} ({percent_served:.02f}% of (2) )')
    print(f'5. Total number of Helpseekers served -- repeated user: {S.served_g_repeated} ({percent_served_repeated:.02f}% of (4) )')
    print(f'6. Total number of Helpseekers served -- user: {S.served_g_regular} ({percent_served_regular:.02f}% of (4) )\n')


    print(f'{Colors.HBLUE}Stage 2b. Number of users reneged given TOS acceptance{Colors.HEND}')
    try:
        percent_reneged = S.reneged/S.num_helpseekers_TOS_accepted * 100
    except ZeroDivisionError:
        percent_reneged = 0
    print(f'7. Total number of Helpseekers reneged: {S.reneged} ({percent_reneged:.02f}% of (2) )\n')


    print(f'{Colors.HBLUE}Queue Status{Colors.HEND}')
    print(f'8. Maximum helpseeker queue length: {S.helpseeker_queue_max_length}')
    print(f'9. Number of instances waiting queue is not empty after first person has been dequeued: {len(S.queue_status)}')
    # print(f'full details: {S.queue_status}')

if __name__ == '__main__':
    main()