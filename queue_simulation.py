'''
    This program uses Simpy to simulate the OpenUp Counselling Service 
    platform and helpseeker arrivals.  

    Helpseekers will renege when they loose patience waiting in the queue

    For more details about the Simpy syntax, please visit
    https://simpy.readthedocs.io/en/latest/contents.html

    Primer in Discrete Event Simulation:
    https://www.academia.edu/35846791/Discrete_Event_Simulation._It_s_Easy_with_SimPy_
'''

import simpy, random, enum
# from scipy.stats import poisson



# Globals
SIMULATION_DURATION = 1440
NUM_COUNSELLING_PROCESS = 16
SEED = 728

################################################################################
# Enums and constants
################################################################################

class Shifts(enum.Enum):
    '''
        different types of shifts
        shift start, end, and next shift offset in minutes
    '''

    GRAVEYARD = ('GRAVEYARD',   1290, 1890, 840, 2) # from 9:30pm to 7:30am
    AM =        ('AM',          435, 915, 960, 2)   # from 7:15am to 3:15 pm
    PM =        ('PM',          840, 1320, 960, 2)  # from 2pm to 10pm
    SPECIAL =   ('SPECIAL',     1020, 1500, 960, 1) # from 5pm to 1 am

    def __init__(self, shift_name, start, end, offset, capacity):
        self.shift_name = shift_name
        self.start = start
        self.end = end
        self.offset = offset
        self.capacity = capacity

    @property
    def duration(self):
        return self.end - self.start

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

    MORNING =   ('MORNING', 600, 840)       # from 10am to 2 pm
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

    CRISIS =    ('CRISIS',  .05)
    HIGH =      ('HIGH',    .015)
    MEDIUM =    ('MEDIUM',  .16)
    LOW =       ('LOW',     .82)

    def __init__(self, risk, probability):
        self.risk = risk
        self.probability = probability

#-------------------------------------------------------------------------------

class Users(enum.Enum):
    '''
        Distribution of Repeated Users - 95% regular / 5% repeated
    '''

    REPEATED =  ('REPEATED USER',   .05) 
    REGULAR =   ('REGULAR USER',    .95) 
    
    def __init__(self, user_type, probability):
        self.user_type = user_type
        self.probability = probability

#-------------------------------------------------------------------------------

class Roles(enum.Enum):
    '''
        Counsellor Roles
    '''

    SOCIAL_WORKER = 'social worker'
    DUTY_OFFICER =  'duty officer'
    VOLUNTEER =     'volunteer'

################################################################################
# Classes
################################################################################

class Counsellor:
    '''
        Class to emulate counselling process
    '''

    lunch_break = 60 # 60 minute lunch break
    mean_chat_duration = 60 # average chat no longer than 60 minutes


    def __init__(self, env, counsellor_id, shift, chatroom_sessions, role):
        '''
            param:

            env - simpy environment instance
            counsellor_id - an assigned counsellor id (INTEGER)
            shift - counsellor shift (one of Shifts enum)
            chatroom_sessions - chatroom sessions FilterStore
        '''

        self.env = env
        self.counsellor = f'Counsellor {counsellor_id}'
        self.chatroom_sessions = chatroom_sessions
        self.lunch = False # whether worker had lunch
        self.adhoc = False # whether worker had performed adhoc duties
        self.shift = shift
        self.shift_remaining = shift.duration
        self.role = role

        # start idle, counselling, adhoc jobs, lunch break, signout states
        self.process = env.process(self.idle() )
        env.process(self.handle_helpseeker() )
        env.process(self.handle_adhoc_jobs() )
        env.process(self.lunch_break() )
        env.process(self.sign_out() )

    #---------------------------------------------------------------------------
    
    def idle(self):
        '''
            counsellor in idle state
            higher priority states will preempt lower priority states
        '''

        while True:
            while self.shift_remaining:
                try:
                    # in idle state
                    start = self.env.now
                    yield self.env.timeout(shift_remaining)
                    self.shift_remaining = 0

                # one of four processes throwing an interrupt
                except simpy.Interrupt as interrupt:
                    cause = interrupt.cause 


                    if cause is JobStates.SIGNOUT:
                        self.signed_out = True

                        print(f'{self.counsellor} shift ends at {self.env.now}')
                        with state.request(priority=cause.priority) as state:
                            yield state & self.env.timeout(self.shift.offset)

                        print(f'{self.counsellor} shift starts at {self.env.now}')

                        # reset all flags
                        self.signed_out = False
                        self.lunch = False
                        self.adhoc = False
                        self.shift_remaining = shift.duration

                    

                    elif cause is JobStates.AD_HOC:
                        self.adhoc = True

                        with state.request(priority=cause.priority) as state:
                            yield state & self.env.timeout(240)



                    elif cause is JobStates.LUNCH:
                        # give lunch break
                        self.lunch = True
                        print(f'{self.counsellor} Requesting a lunch break at '
                            f'{self.env.now}')

                        with state.request(priority=cause.priority) as state:
                            yield state & self.env.timeout(self.lunch_break)



                    elif cause is JobStates.COUNSELLING:
                        # remove helpseeker from queue
                        helpseeker = yield self.chatroom_sessions.get()

                        # serve helpseeker
                        chat_duration = self.assign_chat_duration()
                        with state.request(priority=cause.priority) as state:
                            yield state & self.env.timeout(chat_duration)
                        


                        print(f'{helpseeker} chat session terminated successfully at t ='
                            f' {self.env.now}. Chat lasted {chat_duration}') 
                        

                    # update shift_remaining
                    self.shift_remaining -= self.env.now - start

    #---------------------------------------------------------------------------

    def handle_helpseeker(self):
        pass
        
    #---------------------------------------------------------------------------

    def lunch_break(self):
        '''
            Give counsellors a lunch break
        '''
        while True:
            if not self.lunch and self.shift_remaining < 240:
                self.process.interrupt(JobStates.LUNCH)

    #---------------------------------------------------------------------------

    def handle_adhoc_jobs(self):
        '''
            Handle Ad Hoc Jobs once a day
        '''
        while True:
            yield self.env.timeout(1440)
            if not self.adhoc and self.shift in (Shifts.AM, Shifts.PM):
                self.process.interrupt(JobStates.AD_HOC)                

    #---------------------------------------------------------------------------

    def sign_out(self):
        '''
            function to simulate counsellor off time while they are not at work
        '''
        while True:
            yield self.env.timeout(1440)
            if not self.signed_out:
                self.process.interrupt(JobStates.SIGNOUT)

    #---------------------------------------------------------------------------

    def assign_chat_duration(self):
        '''
            Getter to assign chat duration
            chat duration follows an exponential distribution
        '''
        lambda_chat_duration = 1.0 / self.mean_chat_duration
        return random.expovariate(lambda_chat_duration)

#--------------------------------------------------------end of Counsellor class

class Helpseeker:
    '''
        Helpseeker Class to create a helpseeker
    '''

    mean_interarrival_time = 7.0 # mean time between helpseekers arriving
    mean_renege_time = 7.0  # mean patience before reneging 
                            # specify this as a float

    def __init__(self, env, helpseeker_id, chatroom_sessions):

        '''
            param:
                env - simpy environment instance
                helpseeker_id - an assigned helpseeker id (INTEGER)
                chatroom_sessions - chatroom sessions (FilterStore)
        '''

        self.env = env
        self.arrival_time = None
        self.user = f'Helpseeker {helpseeker_id}'
        self.chatroom_sessions = chatroom_sessions

        # assign random helpseeker risklevels, repeated/regular status
        self.risklevel = random.choices(list(Risklevels) ) 
        self.user_status = random.choices(list(Users) )
        
        # start creating a chatroom session process
        self.process = env.process(self.session() )

    #---------------------------------------------------------------------------

    def session(self):
        '''
            Function to create a chatroom session
        '''

        # simulate the interarrivals between users
        interarrival_time = self.assign_interarrival_time()
        yield self.env.timeout(interarrival_time)

        self.arrival_time = self.env.now

        # request a counselling session
        request = chatroom_sessions.put(self.user)
        print(f'{self.user} has accepted TOS.  '
            f'Chat session created at t = {self.arrival_time}.')

        # wait for counsellor or renege
        patience = self.assign_renege_time()
        results = yield request | self.env.timeout(patience)

        if request not in results: # helpseeker reneged
            print(f'{self.user} reneged after '
                f'spending t = {patience} in the queue.')

            # remove user from the chatroom session store
            yield self.chatroom_sessions.get(lambda x: x==self.user)

        else: # counsellor now serving helpseeker
            # figure out how long user actually spent in the queue
            time_now = self.env.now
            queue_time = time_now - self.arrival_time
            print(f'{self.user} now being served at {time_now}.  '
                f'User spent {queue_time} in the queue.')

    #---------------------------------------------------------------------------

    def assign_interarrival_time(self):
        '''
            Getter to assign interarrival time
            interarrival time follows an exponential distribution
        '''
        lambda_interarrival = 1.0/self.mean_interarrival_time
        return random.expovariate(lambda_interarrival)

    #---------------------------------------------------------------------------

    def assign_renege_time(self):
        '''
            Getter to assign patience to helpseeker
            helpseeker patience follows an exponential distribution

        '''
        lambda_renege = 1.0/self.mean_renege_time
        return random.expovariate(lambda_renege)

#-------------------------------------------------------------------------------

class ServiceOperation:
    '''
        Class to emulate OpenUp Service Operation with a limited number of 
        counsellors to handle helpseeker chat requests

        counsellor availability varies over time

        Helpseekers have to request a counsellor to begin the counselling
        process
    '''

    total_recruits = 0 # total number of recruits
    for shift in list(Shifts):
        total_recruits += shift.capacity

    #---------------------------------------------------------------------------

    def __init__(self, env, shift):
        self.env = env
        self.counsellors_active = simpy.FilterStore(env)
        
        # preset counsellor store
        self.start_shift(shift)

    #---------------------------------------------------------------------------

    def start_shift(self, shift, chatroom_sessions):
        '''
            function to put counsellors 
            into the store at the start of the shift
        '''

        for i in range(shift.capacity+1):
            counsellor = self.env.process(
                Counsellor(self.env, i, shift_duration, chatroom_sessions, role)
            )
            self.counsellors_active.put(counsellor)

    #---------------------------------------------------------------------------

    def end_shift(self, shift):
        '''
            function to remove all counsellors 
            from the store at the end of shift
        '''
        for i in range(shift.capacity+1):
            self.counsellors_active.get(lambda x: x.shift is shift)

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
    
    helpseekers = []
    start_time = env.now
    duration = 0


    # create helpseekers
    for i in range(1, 120):
        helpseekers.append(Helpseeker(env, i, openup_counselling) )

    print(f'Total number of helpseekers created: {len(helpseekers)}\n\n')

    env.run(until=SIMULATION_DURATION) # daily simulation at the queue


if __name__ == '__main__':
    main()