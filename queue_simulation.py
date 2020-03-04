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
    '~/csrp/openup-analysis/interarrivals_day_of_week_hour.csv')

################################################################################ 
# Globals
################################################################################

QUEUE_THRESHOLD = 0                         # memoize data if queue is >= threshold 
DAYS_IN_WEEK = 7                            # 7 days in a week
MINUTES_PER_HOUR = 60                       # 60 minutes in an hour

MAX_SIMULTANEOUS_CHATS_SOCIAL_WORKER = 4    # Social Worker can process max 1 chat
MAX_SIMULTANEOUS_CHATS_DUTY_OFFICER = 1     # Duty Officer can process max 1 chat
MAX_SIMULTANEOUS_CHATS_VOLUNTEER = 1        # Volunteer can process max 1 chat

SEED = 728                                  # for seeding the sudo-random generator
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR     # 1440 minutes per day
SIMULATION_DURATION = MINUTES_PER_DAY * 30  # currently given as num minutes 
                                            #     per day * num days in month

POSTCHAT_FILLOUT_TIME = 20                  # time to fill out counsellor postchat
MEAN_RENEGE_TIME = 7.0                      # mean patience before reneging


# average chat no longer than 60 minutes
MEAN_CHAT_DURATION_SOCIAL_WORKER = 52.4
MEAN_CHAT_DURATION_DUTY_OFFICER = 58.5
MEAN_CHAT_DURATION_VOLUNTEER = 55.7


TEA_BREAK_DURATION = 15                     # 20 minute tea break
MEAL_BREAK_DURATION = 60                    # 60 minute meal break
DEBRIEF_DURATION = 60                       # 60 minute debriefing session per day
TRAINING_DURATION = 480                     # 8 hour training session - once per month
LAST_CASE_CUTOFF = 5 

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
        different types of paid worker shifts
        shift start, end, and next shift offset in minutes
    '''

    GRAVEYARD = ('GRAVEYARD',   True, 1290, 1890, 840, 1, 1, 1290, 30, 435, 15) # from 9:30pm to 7:30am
    AM =        ('AM',          False, 435, 915, 960, 1, 1, 435, 15, 840, 15)   # from 7:15am to 3:15 pm
    PM =        ('PM',          False, 840, 1320, 960, 2, 1, 840, 15, 1290, 30)  # from 2pm to 10pm
    SPECIAL =   ('SPECIAL',     True, 1020, 1500, 960, 2, 0, None, None, None, None) # from 5pm to 1 am

    def __init__(self, shift_name, is_edge_case, start, end, offset,
        num_social_workers, num_duty_officers,
        first_debriefing, first_debriefing_duration,
        last_debriefing, last_debriefing_duration):

        self.shift_name = shift_name
        self.is_edge_case = is_edge_case
        self.start = start
        self.end = end
        self.offset = offset
        self.num_social_workers = num_social_workers
        self.num_duty_officers = num_duty_officers
        # self.first_debriefing = first_debriefing
        # self.first_debriefing_duration = first_debriefing_duration
        # self.last_debriefing = last_debriefing
        # self.last_debriefing_duration = last_debriefing_duration

    @property
    def duration(self):
        return int(self.end - self.start)  


    @property
    def lunch_start(self):
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
    def num_workers(self):
        '''
            returns the number of paid workers during each shift
        '''
        return self.num_social_workers + self.num_duty_officers
    
#-------------------------------------------------------------------------------

class VolunteerShifts(enum.Enum):
    '''
        different types of unpaid worker shifts
        shift start, end, and next shift offset in minutes
    '''

    GRAVEYARD = ('GRAVEYARD',   True, 1200, 1440, 1200, 2)  # from 8pm to 12am
    AM =        ('AM',          False, 630, 870, 1200, 2)   # from 10:30am to 2:30 pm
    PM =        ('PM',          False, 900, 1140, 1200, 2)  # from 3pm to 7pm
    SPECIAL =   ('SPECIAL',     False, 1080, 1320, 1200, 2)  # from 6pm to 10pm

    def __init__(self, shift_name, is_edge_case, start, end, offset,
        num_volunteers):

        self.shift_name = shift_name
        self.is_edge_case = is_edge_case
        self.start = start
        self.end = end
        self.offset = offset
        self.num_volunteers = num_volunteers

    @property
    def duration(self):
        return int(self.end - self.start)

    @property
    def tea_start(self):
        '''
            tea break two hours after the shift has started
        '''
        return int(self.start + 120)

#-------------------------------------------------------------------------------

# class JobStates(enum.Enum):
#     '''
#         Counsellor in three states:
#         counselling, eating lunch, and adhoc duties,
#         each of which are given different priorities (must be integers)

#         The higher the priority, the lower the value 
#         (10 has higher priority than 20)
#     '''

#     SIGNOUT =       ('SIGNED OUT',      10)
#     COUNSELLING =   ('COUNSELLING',     20)
#     LUNCH =         ('EATING LUNCH',    20)
#     AD_HOC =        ('AD HOC DUTIES',   30)

#     def __init__(self, job_name, priority):
#         self.job_name = job_name
#         self.priority = priority

# #-------------------------------------------------------------------------------

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

class Roles(enum.Enum):
    '''
        Counsellor Roles

        # TODO: add repeated/non-repeated user mean chat duration
    '''

    SOCIAL_WORKER = ('SOCIAL_WORKER',   MAX_SIMULTANEOUS_CHATS_SOCIAL_WORKER , 52.4)
    DUTY_OFFICER =  ('DUTY_OFFICER',    MAX_SIMULTANEOUS_CHATS_DUTY_OFFICER , 58.5)
    VOLUNTEER =     ('VOLUNTEER',       MAX_SIMULTANEOUS_CHATS_VOLUNTEER , 55.7)

    def __init__(self, counsellor_type, num_processes, mean_chat_duration):
        self.counsellor_type = counsellor_type
        self.num_processes = num_processes
        self.mean_chat_duration = mean_chat_duration

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
        # self.adhoc_completed = False # whether worker had completed adhoc duty time slice
        # self.adhoc_duty = None # to be set later
        
        self.shift = shift
        self.role = role
        self.priority = None # to be set later

    #---------------------------------------------------------------------------
    # Interrupts and Interrupt Service Routines (ISR)
    #---------------------------------------------------------------------------

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
        self.times_queue_not_empty = []

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
        for vs in VolunteerShifts:
            self.counsellors[vs] = []
            self.create_counsellors(vs)

        # print('Counsellors Arranged:')
        # pprint(self.counsellors)

        # set up signin schedule
        self.counsellor_procs_signin = [self.env.process(
            self.counsellors_signin(s) ) for s in Shifts]
        self.volunteer_procs_signin = [self.env.process(
            self.counsellors_signin(s) ) for s in VolunteerShifts]

        # pprint(self.volunteer_procs_signin)

        # set up meal break schedule
        self.sw_procs_meal = [self.env.process(
            self.counsellors_break(s, Roles.SOCIAL_WORKER, s.lunch_start,
                self.__meal_break, 'meal') ) for s in Shifts]

        self.do_procs_meal = [self.env.process(
            self.counsellors_break(s, Roles.DUTY_OFFICER, s.lunch_start,
                self.__meal_break, 'meal') ) for s in Shifts]

        # set up tea breaks
        # paid counsellors get two tea breaks
        # volunteers get one tea break
        self.sw_procs_first_tea = [self.env.process(
            self.counsellors_break(s, Roles.SOCIAL_WORKER, s.first_tea_start,
                self.__tea_break, 'first tea') ) for s in Shifts]
        self.sw_procs_last_tea = [self.env.process(
            self.counsellors_break(s, Roles.SOCIAL_WORKER, s.last_tea_start,
                self.__tea_break, 'last tea') ) for s in Shifts]
        self.do_procs_first_tea = [self.env.process(
            self.counsellors_break(s, Roles.DUTY_OFFICER, s.first_tea_start,
                self.__tea_break, 'first tea') ) for s in Shifts]
        self.do_procs_last_tea = [self.env.process(
            self.counsellors_break(s, Roles.DUTY_OFFICER, s.last_tea_start,
                self.__tea_break, 'last tea') ) for s in Shifts]
        self.volunteer_procs_tea = [self.env.process(
            self.counsellors_break(s, Roles.VOLUNTEER, s.tea_start,
                self.__tea_break, 'tea') ) for s in VolunteerShifts]

        # set up signout schedule
        self.counsellor_procs_signout = [self.env.process(
            self.counsellors_signout(s) ) for s in Shifts]
        self.volunteer_procs_signout = [self.env.process(
            self.counsellors_signout(s) ) for s in VolunteerShifts]

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
            shift - one of Shifts/Volunteer Shifts enum
        '''

        def create(role, id_):
            for subprocess_num in range(1, role.num_processes+1):
                counsellor_id = f'{shift.shift_name}_{role.counsellor_type}_{id_}_process_{subprocess_num}'
                self.counsellors[shift].append(
                    Counsellor(self.env, counsellor_id, shift, role)
                )

        if shift in Shifts:
            # signing in involves creating multiple counsellor processes
            for id_ in range(1, shift.num_social_workers+1):
                create(Roles.SOCIAL_WORKER, id_)
            
            for id_ in range(1, shift.num_duty_officers+1):
                create(Roles.DUTY_OFFICER, id_)

        elif shift in VolunteerShifts:
            for id_ in range(1, shift.num_volunteers+1):
                create(Roles.VOLUNTEER, id_)


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
                # print(f'\n{Colors.GREEN}+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}')
                # print(f'{Colors.GREEN}Counsellor {counsellor.counsellor_id} signed in at t = {self.env.now}{Colors.WHITE}')
                # print(f'{Colors.GREEN}+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++{Colors.WHITE}\n')

                yield self.store_counsellors_active.put(counsellor)

            # print(f'Signed in shift:{shift.shift_name} at {self.env.now}.  Idle SO counsellor processes:')
            # self.print_idle_counsellors_working()
            # print()

            if counsellor_init and shift.is_edge_case:
                yield self.env.timeout(shift.start)
                counsellor_init = False

            else:
                # repeat every 24 hours
                yield self.env.timeout(MINUTES_PER_DAY)

    #---------------------------------------------------------------------------

    def counsellors_break(self, shift, role, start, duration, break_type):
        '''
            handle to give counsellors a break after a certain delay

            param:
            shift - one of Shifts enum
            role - one of Roles enum
            start - time interval to start break 
            duration - duration in minutes
        '''
        total_procs = 0

        if shift in Shifts:
            if role is Roles.SOCIAL_WORKER:
                total_procs = shift.num_social_workers * role.num_processes
            elif role is Roles.DUTY_OFFICER:
                total_procs = shift.num_duty_officers * role.num_processes

        elif shift in VolunteerShifts and role is Roles.VOLUNTEER:
            total_procs = shift.num_volunteers * role.num_processes



        yield self.env.timeout(start % MINUTES_PER_DAY)
        while True:
            # allow only counsellors at a role and a shift to take break
            counsellor_procs = [self.store_counsellors_active.get(
                lambda x: x.shift is shift and x.role is role)
                for _ in range(total_procs)]

            # wait for all procs
            counsellor = yield AllOf(self.env, counsellor_procs)

            # get all nested counsellor instances
            counsellor_instances = [counsellor[list(counsellor)[i]] for i in range(total_procs)]

            # for c in counsellor_instances:
            #     print(f'\n{Colors.BLUE}*********************************************************************{Colors.WHITE}')
            #     print(f'{Colors.BLUE}Counsellor {c.counsellor_id} AFK for {break_type} at t = {self.env.now}{Colors.WHITE}')
            #     print(f'{Colors.BLUE}*********************************************************************{Colors.WHITE}\n')
            # print(f'lunch shift:{shift.shift_name}, {role} at {self.env.now}.  Idle SO counsellor processes:\n')
            # self.print_idle_counsellors_working()
            # print()

            yield self.env.timeout(duration) # break for duration minutes

            # back to work
            # put filterstoreget counsellor objects back 
            for c in counsellor_instances:
                # print(f'\n{Colors.BLUE}#####################################################################{Colors.WHITE}')
                # print(f'{Colors.BLUE}Counsellor {c.counsellor_id} BAK from {break_type} break at t = {self.env.now}{Colors.WHITE}')
                # print(f'{Colors.BLUE}#####################################################################{Colors.WHITE}\n')
                yield self.store_counsellors_active.put(c)

            # print(f'BAK shift:{shift.shift_name} at {self.env.now}.  Idle SO counsellor processes:')
            # self.print_idle_counsellors_working()
            # print()

            # repeat every 24 hours - duration
            yield self.env.timeout(MINUTES_PER_DAY-duration)

    #---------------------------------------------------------------------------

    def counsellors_signout(self, shift):
        '''
            routine to sign out counsellors during a shift

            param:
            shift - one of Shifts enum
        '''

        if shift in Shifts:
            total_social_worker_procs = shift.num_social_workers * Roles.SOCIAL_WORKER.num_processes
            total_duty_officer_procs = shift.num_duty_officers * Roles.DUTY_OFFICER.num_processes
            total_procs = total_social_worker_procs + total_duty_officer_procs

        elif shift in VolunteerShifts:
            total_volunteer_procs = shift.num_volunteers * Roles.VOLUNTEER.num_processes
            total_procs = total_volunteer_procs

        # delay for shift.end minutes
        # taking the mod to deal with first initialized graveyard or special shifts (edge cases)
        yield self.env.timeout(shift.end % MINUTES_PER_DAY)
        while True:
            for _ in range(total_procs):
                counsellor = yield self.store_counsellors_active.get(lambda x: x.shift is shift)
            #     print(f'\n{Colors.RED}---------------------------------------------------------------------{Colors.WHITE}')
            #     print(f'{Colors.RED}Counsellor {counsellor.counsellor_id} signed out at t = {self.env.now}{Colors.WHITE}')
            #     print(f'{Colors.RED}---------------------------------------------------------------------{Colors.WHITE}\n')
            # print(f'Signed out shift:{shift.shift_name} at {self.env.now}.  Idle SO counsellor processes:\n')
            # self.print_idle_counsellors_working()
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
        helpseeker_status = self.assign_user_status()
        risklevel = self.assign_risklevel(helpseeker_status)

        # print(f'\n{Colors.HGREEN}*********************************************************************{Colors.HEND}')
        # print(f'{Colors.HGREEN}Helpseeker '
        #         f'{helpseeker_id}-{risklevel}-{helpseeker_status} '
        #         f'has just accepted TOS.  Chat session created at '
        #         f'{self.env.now:.3f}{Colors.HEND}')
        # print(f'{Colors.HGREEN}*********************************************************************{Colors.HEND}\n')


        self.helpseeker_in_system.append(helpseeker_id)
        self.helpseeker_queue.append(helpseeker_id)

        

        
        # if high or crisis case, duty officer takes        
        if risklevel in ['HIGH', 'CRISIS']:
            counsellor_role = [Roles.DUTY_OFFICER]
        else: # otherwise assign to social worker or counsellor
            counsellor_role = [Roles.SOCIAL_WORKER, Roles.VOLUNTEER]

        # wait for a counsellor or renege
        # get only counsellors matching risklevel to role
        # and remaining shift > LAST_CASE_CUTOFF
        with self.store_counsellors_active.get(
            lambda x: x.role in counsellor_role and 
                x.shift.end%MINUTES_PER_DAY - int(self.env.now)%MINUTES_PER_DAY > LAST_CASE_CUTOFF) as counsellor:
            results = yield counsellor | self.env.timeout(renege_time)

            # dequeue helpseeker in the waiting queue
            self.helpseeker_queue.remove(helpseeker_id)

            current_helpseeker_queue_length = len(self.helpseeker_queue)
            if current_helpseeker_queue_length > self.helpseeker_queue_max_length:
                self.helpseeker_queue_max_length = current_helpseeker_queue_length

                # print(f'Updated max queue length to {self.helpseeker_queue_max_length}.\n'
                #     f'Helpseeker Queue: {self.helpseeker_queue}\n\n\n')

            if current_helpseeker_queue_length >= QUEUE_THRESHOLD:
                current_time = self.env.now
                current_day_minutes = int(current_time) % MINUTES_PER_DAY
                # print(f'weekday: {int(current_time / MINUTES_PER_DAY)} - hour: {int(current_day_minutes / 60)}, Queue Length: {current_helpseeker_queue_length}')

                self.times_queue_not_empty.append(
                    (f'weekday:{int(current_time / MINUTES_PER_DAY) % DAYS_IN_WEEK}',
                    f'hour:{int(current_day_minutes / MINUTES_PER_HOUR)}',
                    f'queue_length:{current_helpseeker_queue_length}'
                ))

            # print(f'Helpseeker Queue: {self.helpseeker_queue}')

            # store number of available counsellor processes at time
            self.num_available_counsellor_processes.append(
                (self.env.now, len(self.store_counsellors_active.items) )
            )
            
            if counsellor not in results: # if helpseeker reneged
                # remove helpseeker from system record
                self.helpseeker_in_system.remove(helpseeker_id)
                # print(f'Helpseeker in system: {self.helpseeker_in_system}')

                # print(f'\n{Colors.HRED}*********************************************************************{Colors.HEND}')
                # print(f'{Colors.HRED}Helpseeker {helpseeker_id} reneged after '
                #     f'spending t = {renege_time:.3f} minutes in the queue.{Colors.HEND}')
                # print(f'{Colors.HRED}*********************************************************************{Colors.HEND}\n')
                self.reneged += 1 # update counter
                if helpseeker_status is Users.REPEATED:
                    self.reneged_g_repeated += 1
                else:
                    self.reneged_g_regular += 1
                # context manager will automatically cancel counsellor request



            else: # if counsellor takes in a helpseeker
                counsellor_instance = results[list(results)[0]] #unpack the counsellor instance

                # print(f'\n{Colors.HGREEN}*********************************************************************{Colors.HEND}')
                # print(f'Helpseeker {helpseeker_id} is assigned to '
                #     f'{counsellor_instance.counsellor_id} at {self.env.now:.3f}')
                # print(f'{Colors.HGREEN}*********************************************************************{Colors.HEND}\n')

                
                chat_duration = self.assign_chat_duration(counsellor_instance.role)

                # timeout is chat duration + 20 minutes to fill out postchat survey
                yield self.env.timeout(chat_duration + self.__counsellor_postchat_survey)

                # put the counsellor back into the store, so it will be available
                # to the next helpseeker
                # print(f'\n{Colors.HBLUE}*********************************************************************{Colors.HEND}')
                # print(f'{Colors.HBLUE}Helpseeker {helpseeker_id}\'s counselling session lasted t = '
                #     f'{chat_duration:.3f} minutes.\nCounsellor {counsellor_instance.counsellor_id} '
                #     f'is now available at {self.env.now:.3f}.{Colors.HEND}')
                # print(f'{Colors.HBLUE}*********************************************************************{Colors.HEND}\n')

                

                # remove helpseeker from system record
                self.helpseeker_in_system.remove(helpseeker_id)
                # print(f'Helpseeker in system: {self.helpseeker_in_system}')

                yield self.store_counsellors_active.put(counsellor_instance)

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

    def assign_chat_duration(self, role):
        '''
            Getter to assign chat duration
            chat duration follows the gamma distribution (exponential if a=1)

            param: role - one of role enum
        '''
        lambda_chat_duration = 1.0 / role.mean_chat_duration
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
        probability = [x.value[1] for x in options]

        return random.choices(options, probability)[0]

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
                weekday_hours = [float(i.split(',')[-1][:-1])
                    for i in f.readlines()[1:] ]

            return weekday_hours

        except Exception as e:
            print('Unable to read interarrivals file.')

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

    random.seed(SEED) # comment out line if not reproducing results

    # # create environment
    env = simpy.Environment() 

    # set up service operation and run simulation until  
    S = ServiceOperation(env=env)
    env.run(until=SIMULATION_DURATION)
    # # print(S.assign_risklevel() )

    print('\n\n\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    print(f'Final Results ')#-- number of simultaneous chats: {MAX_NUM_SIMULTANEOUS_CHATS}')
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    print(f'Total number of Helpseekers visited OpenUp: {S.num_helpseekers}\n')

    try:
        percent_served = S.served/S.num_helpseekers * 100
    except ZeroDivisionError:
        percent_served = 0
    print(f'Total number of Helpseekers served: {S.served} ({percent_served:.02f}%)')
    print(f'Total number of Helpseekers served -- repeated user: {S.served_g_repeated}')
    print(f'Total number of Helpseekers served -- user: {S.served_g_regular}\n')

    try:
        percent_reneged = S.reneged/S.num_helpseekers * 100
    except ZeroDivisionError:
        percent_reneged = 0
    print(f'Total number of Helpseekers reneged: {S.reneged} ({percent_reneged:.02f}%)')
    print(f'Total number of Helpseekers reneged -- repeated user: {S.reneged_g_repeated}')
    print(f'Total number of Helpseekers reneged -- user: {S.reneged_g_regular}\n')

    print(f'Maximum helpseeker queue length: {S.helpseeker_queue_max_length}')
    print(f'Number of instances waiting queue is not empty after first person has been dequeued: {len(S.times_queue_not_empty)}')
    # print(f'full details: {S.times_queue_not_empty}')

if __name__ == '__main__':
    main()